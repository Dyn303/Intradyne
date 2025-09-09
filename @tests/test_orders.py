from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict

from src.api.orders import submit_order
from src.risk.guardrails import Guardrails, OrderReq, Ledger, ShariahPolicy


class StubPrices:
    def __init__(self, p_now: float, p_1h: float):
        self.p_now = p_now
        self.p_1h = p_1h

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        if at is None:
            return self.p_now
        # anything ~1h old returns past price
        return self.p_1h if (datetime.utcnow() - at) >= timedelta(minutes=59) else self.p_now


class StubRisk:
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


def test_blocked_by_shariah_returns_error(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series(), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )

    ok, payload = submit_order(gr, OrderReq(symbol="DOGE/USDT", side="buy", qty=1), lambda o: {"status": "noop"})
    assert not ok
    assert payload.get("error") in ("block", "halt", "pause")
    # Ensure ledger recorded block
    records = list(ledger.iter_recent(datetime.utcnow() - timedelta(days=1)))
    assert any(r.get("event") == "order_blocked" for r in records)


def test_allowed_executes_and_logs(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series(), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )

    def exec_ok(o: OrderReq) -> Dict:
        return {"order_id": "ABC123", "status": "accepted", "venue": "binance"}

    ok, payload = submit_order(gr, OrderReq(symbol="BTC/USDT", side="buy", qty=2), exec_ok)
    assert ok and payload.get("status") == "accepted"
    records = list(ledger.iter_recent(datetime.utcnow() - timedelta(days=1)))
    assert any(r.get("event") == "order_allowed" for r in records)


def test_var_stepdown_affects_executed_qty(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    returns = [0.001] * 25 + [-0.09] * 5
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series(), returns=returns),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    executed_qty = {}

    def exec_capture(o: OrderReq) -> Dict:
        executed_qty["qty"] = o.qty
        return {"order_id": "XYZ", "status": "accepted"}

    ok, _ = submit_order(gr, OrderReq(symbol="BTC/USDT", side="buy", qty=10), exec_capture)
    assert ok
    assert executed_qty["qty"] < 10


def test_flash_crash_blocks_execution(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # 40% drop triggers pause
    gr = Guardrails(
        price_feed=StubPrices(p_now=60.0, p_1h=100.0),
        risk_data=StubRisk(equity=_eq_series(), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    ok, payload = submit_order(gr, OrderReq(symbol="BTC/USDT", side="buy", qty=1), lambda o: {"status": "accepted"})
    assert not ok and payload.get("error") == "pause"

