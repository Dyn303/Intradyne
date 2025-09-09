from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from fastapi.testclient import TestClient

from src.api.app import app
from src.risk.guardrails import Guardrails, ShariahPolicy, Ledger, OrderReq, PriceFeed, RiskData
from src.api.orders import get_engine


class StubPrices(PriceFeed):
    def __init__(self, p_now: float, p_1h: float):
        self.p_now = p_now
        self.p_1h = p_1h

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        if at is None:
            return self.p_now
        return self.p_1h if (datetime.utcnow() - at) >= timedelta(minutes=59) else self.p_now


class StubRisk(RiskData):
    def __init__(self, equity: List[Tuple[datetime, float]], returns: List[float]):
        self._equity = equity
        self._returns = returns

    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        return self._equity

    def equity_daily_returns_30d(self) -> List[float]:
        return self._returns


def _eq_series(flat: float = 100.0) -> List[Tuple[datetime, float]]:
    now = datetime.utcnow()
    return [(now - timedelta(days=i), flat) for i in range(30)][::-1]


def _inject_test_engine():
    # Inject a stable engine into orders.get_engine singleton
    eng = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series(), returns=[0.0] * 30),
        ledger=Ledger(),
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    import src.api.orders as mod

    mod._engine = eng


def test_healthz():
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_risk_status_and_ledger_tail():
    _inject_test_engine()
    c = TestClient(app)
    # Trigger a couple ledger writes by posting an order
    resp = c.post("/orders", json={"symbol": "BTC/USDT", "side": "buy", "qty": 1})
    assert resp.status_code == 200

    r = c.get("/risk/status")
    assert r.status_code == 200
    body = r.json()
    assert "breaches_24h" in body and "thresholds" in body

    r2 = c.get("/ledger/tail", params={"n": 5})
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)

