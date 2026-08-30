import pytest

from intradyne.engine.risk import RiskManager


def test_sizer_respects_max_pos_pct():
    rm = RiskManager(0.015, 0.003, 0.002, 0.03, 0.05, 0.30, 5, 3)
    qty = rm.sizer(10_000.0, 100.0)
    assert abs(qty - 1.5) < 1e-9


def test_sl_tp_levels():
    rm = RiskManager(0.015, 0.003, 0.002, 0.03, 0.05, 0.30, 5, 3)
    sl, tp = rm.sl_tp_levels(100.0)
    assert abs(sl - 99.7) < 1e-9
    assert abs(tp - 100.2) < 1e-9


def test_flash_crash_detection():
    rm = RiskManager(0.015, 0.003, 0.002, 0.03, 0.05, 0.30, 5, 3)
    # feed high price, then drop 35%
    ts = 1000.0
    assert rm.flash_crash_check("BTC/USDT", ts, 100.0) is False
    assert rm.flash_crash_check("BTC/USDT", ts + 1, 65.0) is True


def test_position_capacity_bounds_cumulative_size():
    """max_pos_pct caps the position, not merely each order.

    Regression: sizer() capped a single order while nothing capped the
    resulting position, and the router only refused entries once the count of
    symbols held reached max_concurrent_pos. On one symbol that never fired,
    so entries stacked -- measured at 32x the intended size on real data.
    """
    r = RiskManager(
        max_pos_pct=0.015,
        per_trade_sl_pct=0.003,
        tp_pct=0.002,
        dd_soft=0.03,
        dd_hard=0.05,
        flash_crash_drop_1h=0.30,
        max_concurrent_pos=5,
        kill_switch_breaches=3,
    )
    equity, price = 10_000.0, 100.0

    # Flat: full allowance, 1.5% of 10k = $150 = 1.5 units at $100.
    assert r.position_capacity(equity, price, 0.0) == pytest.approx(1.5)

    # Half-filled: only the remainder is available.
    assert r.position_capacity(equity, price, 0.75) == pytest.approx(0.75)

    # At the cap: nothing more, and never negative.
    assert r.position_capacity(equity, price, 1.5) == pytest.approx(0.0)
    assert r.position_capacity(equity, price, 5.0) == pytest.approx(0.0)


def test_position_capacity_handles_a_zero_price():
    r = RiskManager(
        max_pos_pct=0.015,
        per_trade_sl_pct=0.003,
        tp_pct=0.002,
        dd_soft=0.03,
        dd_hard=0.05,
        flash_crash_drop_1h=0.30,
        max_concurrent_pos=5,
        kill_switch_breaches=3,
    )
    assert r.position_capacity(10_000.0, 0.0, 0.0) == 0.0


def _router(tp_pct=0.008, sl_pct=0.002):
    """A router wired to a real portfolio, for stop-placement checks."""
    from intradyne.engine.execution import ExecContext, ExecutionManager
    from intradyne.engine.broker_paper import PaperBroker
    from intradyne.engine.portfolio import Portfolio
    from intradyne.engine.router import StrategyRouter
    from intradyne.core.ledger import Ledger
    import os
    import tempfile

    portfolio = Portfolio()
    paper = PaperBroker(portfolio, slippage_bps=0)
    ledger = Ledger(path=os.path.join(tempfile.mkdtemp(), "l.jsonl"))
    risk = RiskManager(
        max_pos_pct=0.5,
        per_trade_sl_pct=sl_pct,
        tp_pct=tp_pct,
        dd_soft=0.9,
        dd_hard=0.95,
        flash_crash_drop_1h=0.9,
        max_concurrent_pos=5,
        kill_switch_breaches=99,
    )
    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=ledger,
        whitelist=["BTC/USDT"],
        fast_mode=True,
    )
    return StrategyRouter(
        ["BTC/USDT"], risk, ExecutionManager(ctx), portfolio
    ), portfolio


def test_stop_is_anchored_to_average_cost_not_the_latest_entry():
    """Regression: the stop was recomputed from each new entry price and
    overwrote the previous one, so averaging down walked it down with every
    buy. A 20bps stop then realised losses several times that against average
    cost -- measured to -85bps on real data.
    """
    router, portfolio = _router(sl_pct=0.002)

    # Build a position across two prices: average cost is 99.
    portfolio.buy("BTC/USDT", qty=1.0, price=100.0)
    portfolio.buy("BTC/USDT", qty=1.0, price=98.0)
    pos = portfolio.get_position("BTC/USDT")
    assert pos.avg_price == pytest.approx(99.0)

    # The stop the router should hold is 20bps below average cost, not 20bps
    # below the most recent entry (98.0), which would be materially lower.
    expected_from_avg, _ = router.risk.sl_tp_levels(pos.avg_price)
    expected_from_last, _ = router.risk.sl_tp_levels(98.0)
    assert expected_from_avg > expected_from_last

    # Realised loss if stopped at the average-anchored level is the
    # configured distance; at the latest-entry level it is worse.
    loss_correct = (expected_from_avg - pos.avg_price) / pos.avg_price * 1e4
    loss_buggy = (expected_from_last - pos.avg_price) / pos.avg_price * 1e4
    assert loss_correct == pytest.approx(-20.0, abs=0.5)
    assert loss_buggy < -20.0
