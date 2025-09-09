from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, List

from src.config import load_settings
from src.risk.guardrails import Guardrails, OrderReq, Ledger, ShariahPolicy, PriceFeed, RiskData


class _FlatPrices(PriceFeed):
    def __init__(self, price: float = 100.0) -> None:
        self.price = price

    def get_price(self, symbol, at=None):
        return self.price


class _FlatRisk(RiskData):
    def equity_series_30d(self):
        now = datetime.utcnow()
        return [(now - timedelta(days=i), 100.0) for i in range(30)][::-1]

    def equity_daily_returns_30d(self):
        return [0.0] * 30


def build_engine(symbols: Iterable[str], ledger_path: str | None = None) -> Guardrails:
    settings = load_settings()
    sh = ShariahPolicy(allowed_crypto=settings.allowed_crypto_list())
    path = ledger_path or settings.EXPLAIN_LEDGER_PATH
    return Guardrails(price_feed=_FlatPrices(), risk_data=_FlatRisk(), ledger=Ledger(path), shariah=sh)


def run_backtest(days: int = 1, symbols: Iterable[str] | None = None, ledger_path: str | None = None) -> int:
    symbols = symbols or ["BTC/USDT", "ETH/USDT"]
    eng = build_engine(symbols, ledger_path=ledger_path)
    count = 0
    for _ in range(max(1, days)):
        for s in symbols:
            action, reasons, adj = eng.gate_trade(OrderReq(symbol=s, side="buy", qty=1))
            eng.ledger.append(
                "backtest_order",
                {"symbol": s, "action": action, "reasons": reasons, "qty": adj.qty, "mode": "backtest"},
            )
            count += 1
    return count

