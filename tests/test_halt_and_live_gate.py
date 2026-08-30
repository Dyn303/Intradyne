"""Operator halt as a Tier 1 concern, and the live-trading boot gate."""

from __future__ import annotations

import pytest

from intradyne.core.config import assert_live_trading_gate, load_settings
from intradyne.risk.guardrails import Guardrails, OrderReq, ShariahPolicy
from intradyne.risk.kill_switch import halt_reason, is_halted, set_halt


@pytest.fixture(autouse=True)
def _clear_halt():
    set_halt(False)
    yield
    set_halt(False)


def _guardrails(tmp_path):
    from datetime import datetime
    from typing import List, Optional, Tuple

    from intradyne.core.ledger import Ledger
    from intradyne.risk.guardrails import PriceFeed, RiskData

    class _P(PriceFeed):
        def get_price(self, symbol: str, at: Optional[datetime] = None):
            return None

    class _R(RiskData):
        def equity_series_30d(self) -> List[Tuple[datetime, float]]:
            return []

        def equity_daily_returns_30d(self) -> List[float]:
            return []

    return Guardrails(
        price_feed=_P(),
        risk_data=_R(),
        ledger=Ledger(path=str(tmp_path / "l.jsonl")),
        shariah=ShariahPolicy(allowed_crypto=["BTC/USDT"]),
    )


def test_halt_blocks_orders_at_the_gate(tmp_path):
    """Previously only POST /orders checked the halt, so a strategy-generated
    order would sail past it."""
    gr = _guardrails(tmp_path)
    order = OrderReq(symbol="BTC/USDT", side="buy", qty=1.0)

    assert gr.gate_trade(order)[0] == "allow"

    set_halt(True, reason="manual stop")
    action, reasons, _ = gr.gate_trade(order)
    assert action == "halt"
    assert reasons == ["manual stop"]


def test_halt_refusal_is_recorded_in_the_ledger(tmp_path):
    gr = _guardrails(tmp_path)
    set_halt(True, reason="manual stop")
    gr.gate_trade(OrderReq(symbol="BTC/USDT", side="buy", qty=1.0))

    events = [r.get("event") for r in gr.ledger.iter_all()]
    assert "guardrail_breach" in events
    assert gr.ledger.verify_chain()[0] is True


def test_halt_state_round_trips():
    assert is_halted() is False
    set_halt(True, reason="why")
    assert is_halted() is True and halt_reason() == "why"
    set_halt(False)
    assert is_halted() is False and halt_reason() == ""


def test_live_trading_is_gated_shut(monkeypatch):
    """Phase 5 is not done; arming live must refuse to start."""
    s = load_settings().model_copy(
        update={"mode": "live", "live_trading_enabled": True}
    )
    with pytest.raises(RuntimeError, match="phase 5"):
        assert_live_trading_gate(s)


@pytest.mark.parametrize(
    "mode,enabled",
    [("paper", False), ("paper", True), ("live", False)],
)
def test_gate_allows_every_non_live_combination(mode, enabled):
    s = load_settings().model_copy(
        update={"mode": mode, "live_trading_enabled": enabled}
    )
    assert_live_trading_gate(s)  # must not raise


def test_gate_is_not_env_overridable(monkeypatch):
    """An env override is how this would get flipped by accident; opening the
    gate must be a reviewable code change."""
    for name in ("LIVE_TRADING_GATE_OPEN", "ALLOW_LIVE", "INTRADYNE_ALLOW_LIVE"):
        monkeypatch.setenv(name, "1")
    s = load_settings().model_copy(
        update={"mode": "live", "live_trading_enabled": True}
    )
    with pytest.raises(RuntimeError):
        assert_live_trading_gate(s)
