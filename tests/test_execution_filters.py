"""The spread filter and the slicing count, both measured against Bitget.

`max_spread_bps` defaulted to 0 -- disabled -- so the engine crossed whatever
spread the venue quoted while `slippage_bps` booked every fill at a flat 2.
On the traded whitelist that gap was real: DOT quoted 11.38-22.78bps with
nothing resting within 5bps of the touch.

`micro_slices` defaulted to 3. Slicing avoids moving the book, and at
MAX_ORDER_NOTIONAL=300 a full order is 0.02-1.2% of the depth within 5bps on
the liquid names, so there was no book to move.
"""

from __future__ import annotations

import asyncio

import pytest

from intradyne.core.config import Settings
from intradyne.engine.execution import ExecContext, ExecutionManager
from intradyne.engine.portfolio import Portfolio
from intradyne.engine.broker_paper import PaperBroker
from intradyne.engine.risk import RiskManager
from intradyne.engine.router import StrategyRouter


SYM = "BTC/USDT"


def _router():
    portfolio = Portfolio()
    portfolio.balances["USDT"] = 10_000.0
    paper = PaperBroker(portfolio)
    risk = RiskManager(
        max_pos_pct=0.5,
        per_trade_sl_pct=0.002,
        tp_pct=0.004,
        dd_soft=0.9,
        dd_hard=0.95,
        flash_crash_drop_1h=0.9,
        max_concurrent_pos=5,
        kill_switch_breaches=99,
    )
    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=[],
        whitelist=[SYM],
        fast_mode=True,
    )
    return StrategyRouter([SYM], risk, ExecutionManager(ctx), portfolio), paper


def _quote(mid: float, spread_bps: float):
    half = mid * spread_bps / 2e4
    return {
        "symbol": SYM,
        "bid": mid - half,
        "ask": mid + half,
        "last": mid,
        "ts": 0.0,
    }


def _force_breakout(router, spread_bps: float, ticks: int = 200):
    """Walk the price up so the momentum strategy signals, then count fills."""
    paper_orders = []
    router.execman.ctx.paper.orders = {}
    mid = 100.0
    for i in range(ticks):
        mid *= 1.0005
        asyncio.run(router.on_tick(_quote(mid, spread_bps)))
    paper_orders = list(router.execman.ctx.paper.orders.values())
    return [o for o in paper_orders if o.side == "buy"]


def test_spread_filter_is_on_by_default():
    """It defaulted to 0, which disabled it. A filter that defends the cost
    model has to be running to defend anything."""
    assert Settings().max_spread_bps == 4


def test_default_bound_admits_the_liquid_names_and_excludes_the_thin_ones():
    """Measured medians on Bitget over five samples, 3s apart."""
    bound = Settings().max_spread_bps
    measured = {
        "BTC/USDT": 0.00,
        "ETH/USDT": 0.04,
        "XRP/USDT": 0.69,
        "SOL/USDT": 0.96,
        "AVAX/USDT": 1.33,
        "LTC/USDT": 1.96,
    }
    for sym, spread in measured.items():
        assert spread <= bound, f"{sym} at {spread}bps should still trade"
    for sym, spread in {"ADA/USDT": 4.51, "DOT/USDT": 11.38}.items():
        assert spread > bound, f"{sym} at {spread}bps should be refused"


def test_bound_stays_within_the_cost_model():
    """The bound is 2x the slippage the paper broker charges. Past that the
    model is not describing the fill, so results flatter the thin names."""
    assert Settings().max_spread_bps == 2 * PaperBroker(Portfolio()).slippage_bps


def test_a_wide_spread_produces_no_entry():
    router, _ = _router()
    router._max_spread_bps = Settings().max_spread_bps
    assert _force_breakout(router, spread_bps=12.0) == []


def test_a_tight_spread_still_trades():
    router, _ = _router()
    router._max_spread_bps = Settings().max_spread_bps
    assert _force_breakout(router, spread_bps=1.0), "1bps must not be filtered"


def test_one_entry_is_one_order():
    """micro_slices was 3, so every entry became three child orders 83-152ms
    apart -- far inside book replenishment, sweeping the same levels in three
    bites. At this size there is no book to move."""
    router, _ = _router()
    assert router.micro_slices == 1
    router._max_spread_bps = Settings().max_spread_bps
    buys = _force_breakout(router, spread_bps=1.0)
    assert buys, "expected at least one entry"
    # Group fills by price: one entry must not appear as three equal clips.
    qtys = [round(o.qty, 12) for o in buys]
    assert len(qtys) == len(set(qtys)) or len(qtys) == 1, (
        f"identical sibling quantities suggest slicing is back: {qtys}"
    )


def test_slicing_is_cost_neutral_in_paper_so_it_cannot_be_judged_there():
    """Both cost terms scale with notional -- `fee_for` is `notional * bps`
    and `_apply_slippage` scales price, not size. Splitting an order therefore
    changes nothing at all, which is why this parameter had to be judged
    against real book depth rather than by running the paper loop twice."""
    qty, px = 1.6400366015026446, 103.32

    def round_trip(slices: int) -> float:
        p = Portfolio()
        p.balances["USDT"] = 10_000.0
        rem = qty
        for i in range(slices):
            q = qty / slices if i < slices - 1 else rem
            p.buy(SYM, q, px * (1 + 2 / 10_000))
            rem -= q
        p.sell(SYM, p.get_position(SYM).base, px * (1 - 2 / 10_000))
        return p.balances["USDT"]

    baseline = round_trip(1)
    for slices in (2, 3, 5, 10):
        assert round_trip(slices) == pytest.approx(baseline, abs=1e-9), (
            f"{slices} slices should cost exactly what 1 costs"
        )
