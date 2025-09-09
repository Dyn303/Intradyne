from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import uuid

from fastapi.testclient import TestClient

from src.api.orders import create_app
from src.risk.guardrails import Guardrails, ShariahPolicy, Ledger, OrderReq, PriceFeed, RiskData


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


def _engine(prices: StubPrices, returns=None, allowed=None):
    returns = returns if returns is not None else [0.0] * 30
    allowed = allowed if allowed is not None else {"BTC/USDT"}
    return Guardrails(
        price_feed=prices,
        risk_data=StubRisk(equity=_eq_series(), returns=returns),
        ledger=Ledger(path=f"orders_api_ledger_{uuid.uuid4().hex}.jsonl"),
        shariah=ShariahPolicy(allowed_crypto=allowed),
    )


def test_orders_happy_path():
    eng = _engine(StubPrices(100.0, 100.0))
    app = create_app(engine=eng)
    c = TestClient(app)
    r = c.post("/orders", json={"symbol": "BTC/USDT", "side": "buy", "qty": 1})
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "accepted"
    assert "trade_id" in body


def test_orders_whitelist_reject():
    eng = _engine(StubPrices(100.0, 100.0), allowed={"BTC/USDT"})
    app = create_app(engine=eng)
    c = TestClient(app)
    r = c.post("/orders", json={"symbol": "DOGE/USDT", "side": "buy", "qty": 1})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] in ("block", "halt", "pause")


def test_orders_flash_crash_pause():
    eng = _engine(StubPrices(60.0, 100.0))  # 40% drop
    app = create_app(engine=eng)
    c = TestClient(app)
    r = c.post("/orders", json={"symbol": "BTC/USDT", "side": "buy", "qty": 1})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "pause"


def test_orders_dd_halt():
    # Create risk data with heavy drawdown via custom engine thresholds
    class RiskDD(StubRisk):
        pass

    # Build equity with deep DD
    now = datetime.utcnow()
    equity = [(now - timedelta(days=i), 100.0) for i in range(30)][::-1]
    equity[-1] = (equity[-1][0], 70.0)  # 30% below peak
    rdata = StubRisk(equity=equity, returns=[0.0] * 30)
    eng = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=rdata,
        ledger=Ledger(path=f"orders_api_ledger_{uuid.uuid4().hex}.jsonl"),
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    app = create_app(engine=eng)
    c = TestClient(app)
    r = c.post("/orders", json={"symbol": "BTC/USDT", "side": "buy", "qty": 1})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "halt"





