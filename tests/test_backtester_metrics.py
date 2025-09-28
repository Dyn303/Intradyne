from __future__ import annotations

from src.backtester.engine import compute_daily_returns, compute_max_drawdown


def test_daily_return_pct_formula():
    equity = [100.0, 101.0, 100.0, 105.0]
    rets = compute_daily_returns(equity)
    assert rets[0] == 0.0
    assert round(rets[1], 6) == round((101.0 / 100.0 - 1) * 100, 6)
    assert round(rets[2], 6) == round((100.0 / 101.0 - 1) * 100, 6)
    assert round(rets[3], 6) == round((105.0 / 100.0 - 1) * 100, 6)


def test_max_drawdown_from_running_peak():
    # Peak at 120, drop to 90 => dd = (120-90)/120*100 = 25%
    equity = [100.0, 110.0, 120.0, 115.0, 90.0, 95.0, 130.0]
    mdd = compute_max_drawdown(equity)
    assert round(mdd, 6) == 25.0
