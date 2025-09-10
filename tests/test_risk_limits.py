from __future__ import annotations

from src.core.portfolio import Portfolio


def test_kill_switch_triggers_on_drawdown():
    p = Portfolio(cash=100.0, max_drawdown=0.10)
    # Prices for a fake symbol to update equity path
    prices = {"BTC/USDT": 100.0}
    p.positions["BTC/USDT"] = 0.0
    p.update_equity({"BTC/USDT": 0.0})
    start = p.equity
    # Simulate run-up then drop >10%
    p.max_equity_today = 200.0
    p.equity = 170.0  # 15% drawdown from max
    assert p.check_risk_limits() is True

