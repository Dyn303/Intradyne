"""Maker execution's missed trades, which were invisible.

An unfilled maker order is not a saving, it is a trade the strategy wanted and
did not get. Two paths lost them silently: an order resting past `limit_ttl_s`
had its status set to "expired" and nothing else, and a signal arriving while
an order already rested returned "pending" without a record.

Both are the number Stage 1 of PERFORMANCE_IMPROVEMENT_PLAN.md turns on -- the
fill rate decides whether a lower fee is worth anything -- so measuring the
maker path without them would have produced a figure nobody could interpret.
"""

from __future__ import annotations

import asyncio

from intradyne.engine.broker_paper import PaperBroker
from intradyne.engine.execution import ExecContext, ExecutionManager
from intradyne.engine.portfolio import Portfolio


SYM = "BTC/USDT"
PX = 80_000.0


class FakeLedger(list):
    def append(self, event, payload=None):  # type: ignore[override]
        list.append(self, (event, payload))


def _q(px=PX, ts=0.0):
    return {"symbol": SYM, "bid": px, "ask": px * 1.0001, "last": px, "ts": ts}


def _mgr():
    pf = Portfolio()
    pf.balances["USDT"] = 100_000.0
    ctx = ExecContext(
        portfolio=pf,
        paper=PaperBroker(pf, limit_ttl_s=60.0),
        ledger=FakeLedger(),
        whitelist=[SYM],
        fast_mode=True,
        execution_mode="maker",
        min_entry_notional=0.0,
    )
    return ExecutionManager(ctx), ctx


def _buy(mgr, qty=0.002, ts=0.0):
    return asyncio.run(
        mgr.submit(SYM, "buy", "market", qty, None, _q(ts=ts), "test", {}, {})
    )


class TestExpiry:
    def test_an_order_that_rests_past_its_ttl_is_counted(self):
        mgr, ctx = _mgr()
        _buy(mgr)
        assert ctx.paper.expired == 0

        # Price never comes down to the resting bid; the TTL passes.
        ctx.paper.on_tick(_q(px=PX * 1.01, ts=61.0))
        assert ctx.paper.expired == 1, "the missed trade left no trace"

    def test_an_order_that_fills_is_not_counted_as_expired(self):
        mgr, ctx = _mgr()
        _buy(mgr)
        ctx.paper.on_tick(_q(px=PX * 0.999, ts=1.0))  # market comes to the bid
        assert ctx.paper.expired == 0
        assert ctx.portfolio.get_position(SYM).base > 0

    def test_expiry_is_not_charged_a_fee(self):
        """A missed trade costs opportunity, not money -- if it debited the
        account the fill-rate measurement would be double-counted."""
        mgr, ctx = _mgr()
        before = ctx.portfolio.balances["USDT"]
        _buy(mgr)
        ctx.paper.on_tick(_q(px=PX * 1.01, ts=61.0))
        assert ctx.portfolio.balances["USDT"] == before


class TestSuppression:
    def test_a_signal_arriving_while_an_order_rests_is_recorded(self):
        """One resting entry per symbol is deliberate -- queued orders once
        filled together at twelve times the intended size. But the suppressed
        signals are still trades the strategy wanted."""
        mgr, ctx = _mgr()
        assert _buy(mgr)["status"] != "pending"
        r = _buy(mgr, ts=1.0)
        assert r["action"] == "resting_order_exists"
        assert [e for e in ctx.ledger if "resting_order_exists" in str(e)]

    def test_suppression_stops_once_the_book_is_clear(self):
        mgr, ctx = _mgr()
        _buy(mgr)
        ctx.paper.on_tick(_q(px=PX * 0.999, ts=1.0))  # fills, book clears
        assert _buy(mgr, ts=2.0)["status"] != "pending"


def test_taker_mode_is_untouched():
    """The whole mechanism only applies when execution_mode is maker."""
    pf = Portfolio()
    pf.balances["USDT"] = 100_000.0
    ctx = ExecContext(
        portfolio=pf,
        paper=PaperBroker(pf),
        ledger=FakeLedger(),
        whitelist=[SYM],
        fast_mode=True,
        execution_mode="taker",
        min_entry_notional=0.0,
    )
    mgr = ExecutionManager(ctx)
    for i in range(3):
        assert _buy(mgr, ts=float(i))["status"] != "pending"
    assert ctx.paper.expired == 0
