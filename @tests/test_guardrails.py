from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from src.risk.guardrails import (
    Guardrails,
    OrderReq,
    Ledger,
    ShariahPolicy,
)


class StubPrices:
    def __init__(self, series_now: float, series_1h: float):
        self.now = series_now
        self.past = series_1h

    def get_price(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        if at is None:
            return self.now
        return self.past if (datetime.utcnow() - at) >= timedelta(minutes=59) else self.now


class StubRisk:
    def __init__(self, equity: List[Tuple[datetime, float]], returns: List[float]):
        self._equity = equity
        self._returns = returns

    def equity_series_30d(self) -> List[Tuple[datetime, float]]:
        return self._equity

    def equity_daily_returns_30d(self) -> List[float]:
        return self._returns


def _eq_series_with_dd(dd: float, days: int = 30) -> List[Tuple[datetime, float]]:
    # Peak at 100, trough at 100*(1-dd)
    peak = 100.0
    trough = peak * (1 - dd)
    now = datetime.utcnow()
    series = []
    # rising to peak
    for i in range(days // 2):
        series.append((now - timedelta(days=days - i), 50 + (peak - 50) * (i / max(1, days // 2))))
    # drop to trough and partial recover
    series.append((now - timedelta(days=5), trough))
    series.append((now - timedelta(days=1), trough * 1.05))
    series.append((now, trough * 1.10))
    return series


def test_shariah_whitelist_blocks_unknown(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[]),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )

    action, reasons, _ = gr.gate_trade(OrderReq(symbol="DOGE/USDT", side="buy", qty=1))
    assert action == "block"
    assert any("allowed" in r or "compliance" for r in reasons)


def test_dd_halt(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    eq = _eq_series_with_dd(0.25)
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=eq, returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "halt"
    assert any("drawdown" in r for r in reasons)


def test_flash_crash_pause(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # 35% drop over 1h
    gr = Guardrails(
        price_feed=StubPrices(series_now=65.0, series_1h=100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "pause"
    assert any("flash_crash" in r for r in reasons)


def test_kill_switch_halts_on_recent_breaches(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # Pre-populate with 3 breaches
    for _ in range(3):
        ledger.append("guardrail_breach", {"type": "dd_warn", "action": "warn"})
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "halt"
    assert "kill_switch" in reasons


def test_var_stepdown_adjusts_qty(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # Returns with heavy negative tail so VaR > 5%
    returns = [0.001] * 25 + [-0.08] * 5
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=returns),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, out_req = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=100))
    assert action == "allow"
    assert out_req.qty < 100
    assert any("var" in r for r in reasons)


def test_ledger_hash_chain(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    r1 = ledger.append("guardrail_breach", {"type": "test1", "action": "warn"})
    r2 = ledger.append("guardrail_breach", {"type": "test2", "action": "halt"})
    assert r1["hash"] and r2["hash_prev"] == r1["hash"]


def test_flash_crash_exact_threshold_no_pause(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # Exactly 30% drop should NOT pause (threshold uses >)
    gr = Guardrails(
        price_feed=StubPrices(series_now=70.0, series_1h=100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "allow"


def test_flash_crash_just_over_threshold_pauses(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    # 31% drop should pause
    gr = Guardrails(
        price_feed=StubPrices(series_now=69.0, series_1h=100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "pause"


def test_kill_switch_not_triggered_with_two_breaches(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    for _ in range(2):
        ledger.append("guardrail_breach", {"type": "dd_warn", "action": "warn"})
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1))
    assert action == "allow"


def test_shariah_blocked_tags_rejects(tmp_path):
    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    gr = Guardrails(
        price_feed=StubPrices(100.0, 100.0),
        risk_data=StubRisk(equity=_eq_series_with_dd(0.0), returns=[0.0] * 30),
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto={"BTC/USDT"}),
    )
    action, reasons, _ = gr.gate_trade(
        OrderReq(symbol="BTC/USDT", side="buy", qty=1, meta={"tags": ["gambling"]})
    )
    assert action == "block"
