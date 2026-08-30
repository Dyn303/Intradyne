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
