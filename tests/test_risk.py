from app.risk import RiskManager


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

