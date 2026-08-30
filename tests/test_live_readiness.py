"""Phase 5 controls: exposure caps, idempotency, reconciliation, triple gate.

The gate is deliberately still shut -- see test_the_live_gate_remains_closed.
These verify the controls that must exist before it can be opened.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from intradyne.core.idempotency import DuplicateOrder, OrderKeyStore, make_key
from intradyne.core.ledger import Ledger
from intradyne.core.limits import NotionalTracker
from intradyne.engine.reconcile import find_unreconciled, reconcile_on_start
from intradyne.risk.guardrails import Guardrails, OrderReq, PriceFeed, RiskData
from intradyne.risk.kill_switch import is_halted, set_halt
from intradyne.risk.shariah import ComplianceError, ShariahPolicy


@pytest.fixture(autouse=True)
def _clean_halt():
    set_halt(False)
    yield
    set_halt(False)


def _gate(tmp_path, limits=None, **thresholds):
    class _P(PriceFeed):
        def get_price(self, symbol, at=None):
            return None

    class _R(RiskData):
        def equity_series_30d(self):
            return []

        def equity_daily_returns_30d(self):
            return []

    return Guardrails(
        price_feed=_P(),
        risk_data=_R(),
        ledger=Ledger(path=str(tmp_path / "l.jsonl")),
        shariah=ShariahPolicy(allowed_crypto=["BTC/USDT"]),
        thresholds=thresholds or None,
        limits=limits,
    )


def _buy(qty=1.0, price=100.0):
    return OrderReq(symbol="BTC/USDT", side="buy", qty=qty, price=price)


# ---- exposure caps -------------------------------------------------------


def test_no_caps_configured_means_no_cap_checks(tmp_path):
    assert _gate(tmp_path).gate_trade(_buy())[0] == "allow"


def test_per_order_notional_cap_blocks(tmp_path):
    gr = _gate(tmp_path, max_order_notional=500.0)
    assert gr.gate_trade(_buy(qty=1.0, price=100.0))[0] == "allow"
    action, reasons, _ = gr.gate_trade(_buy(qty=10.0, price=100.0))
    assert action == "block"
    assert "exceeds cap" in reasons[0]


def test_cap_fails_closed_when_the_order_has_no_price(tmp_path):
    """A cap that cannot be evaluated must not be treated as satisfied."""
    gr = _gate(tmp_path, max_order_notional=500.0)
    action, reasons, _ = gr.gate_trade(
        OrderReq(symbol="BTC/USDT", side="buy", qty=1.0, price=None)
    )
    assert action == "block"
    assert "cannot verify notional cap" in reasons[0]


def test_cumulative_caps_without_a_tracker_fail_closed(tmp_path):
    gr = _gate(tmp_path, max_daily_notional=1000.0)
    action, reasons, _ = gr.gate_trade(_buy())
    assert action == "block"
    assert "no tracker" in reasons[0]


def test_per_symbol_24h_cap_accumulates(tmp_path):
    tracker = NotionalTracker(f"sqlite:///{tmp_path / 'n.sqlite'}")
    gr = _gate(tmp_path, max_symbol_notional_24h=1000.0, limits=tracker)

    assert gr.gate_trade(_buy(qty=5.0, price=100.0))[0] == "allow"  # 500
    tracker.record("BTC/USDT", 500.0)
    assert gr.gate_trade(_buy(qty=4.0, price=100.0))[0] == "allow"  # 500 + 400
    tracker.record("BTC/USDT", 400.0)
    action, reasons, _ = gr.gate_trade(_buy(qty=5.0, price=100.0))  # would be 1400
    assert action == "block"
    assert "24h notional" in reasons[0]


def test_daily_cap_spans_symbols(tmp_path):
    tracker = NotionalTracker(f"sqlite:///{tmp_path / 'n.sqlite'}")
    gr = _gate(tmp_path, max_daily_notional=1000.0, limits=tracker)
    tracker.record("ETH/USDT", 900.0)
    action, reasons, _ = gr.gate_trade(_buy(qty=5.0, price=100.0))
    assert action == "block"
    assert "total notional" in reasons[0]


def test_old_notional_falls_out_of_the_window(tmp_path):
    tracker = NotionalTracker(f"sqlite:///{tmp_path / 'n.sqlite'}")
    tracker.record(
        "BTC/USDT", 5000.0, ts=datetime.now(timezone.utc) - timedelta(hours=30)
    )
    assert tracker.symbol_notional("BTC/USDT", hours=24.0) == pytest.approx(0.0)
    gr = _gate(tmp_path, max_symbol_notional_24h=1000.0, limits=tracker)
    assert gr.gate_trade(_buy(qty=1.0, price=100.0))[0] == "allow"


def test_cap_breach_is_recorded_in_the_ledger(tmp_path):
    gr = _gate(tmp_path, max_order_notional=10.0)
    gr.gate_trade(_buy(qty=10.0, price=100.0))
    breaches = [r for r in gr.ledger.iter_all() if r.get("event") == "guardrail_breach"]
    assert breaches and breaches[0]["type"] == "notional_cap"
    assert gr.ledger.verify_chain()[0] is True


# ---- idempotency ---------------------------------------------------------


def test_the_same_intent_produces_the_same_key():
    at = 1_000_000.0
    a = make_key("BTC/USDT", "buy", 1.0, "momo", at=at)
    b = make_key("BTC/USDT", "buy", 1.0, "momo", at=at + 1)
    assert a == b, "a replayed intent must be recognised as the same order"


def test_different_intents_produce_different_keys():
    at = 1_000_000.0
    base = make_key("BTC/USDT", "buy", 1.0, "momo", at=at)
    assert make_key("ETH/USDT", "buy", 1.0, "momo", at=at) != base
    assert make_key("BTC/USDT", "sell", 1.0, "momo", at=at) != base
    assert make_key("BTC/USDT", "buy", 2.0, "momo", at=at) != base


def test_a_key_can_only_be_claimed_once(tmp_path):
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    with pytest.raises(DuplicateOrder):
        store.reserve("idy-1", "BTC/USDT", "buy", 1.0)


def test_a_claim_survives_a_restart(tmp_path):
    """The whole point: a crash mid-submit must not free the key."""
    db = f"sqlite:///{tmp_path / 'k.sqlite'}"
    OrderKeyStore(db).reserve("idy-1", "BTC/USDT", "buy", 1.0)
    with pytest.raises(DuplicateOrder):
        OrderKeyStore(db).reserve("idy-1", "BTC/USDT", "buy", 1.0)


def test_a_failed_submission_keeps_the_key_claimed(tmp_path):
    """The venue may have received it, so the key must not be reusable."""
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    store.fail("idy-1")
    assert store.status("idy-1") == "failed"
    with pytest.raises(DuplicateOrder):
        store.reserve("idy-1", "BTC/USDT", "buy", 1.0)


# ---- restart reconciliation ---------------------------------------------


def test_completed_orders_need_no_reconciliation(tmp_path):
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    store.complete("idy-1", venue_id="X1")
    result = reconcile_on_start(store, live=True, grace_seconds=0.0)
    assert result["unreconciled"] == [] and result["halted"] is False
    assert is_halted() is False


def test_an_unresolved_claim_halts_trading_on_restart(tmp_path):
    """It cannot be known whether the venue received it, so the system stops
    rather than guessing."""
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    result = reconcile_on_start(store, live=True, grace_seconds=0.0)
    assert result["halted"] is True
    assert len(result["unreconciled"]) == 1
    assert is_halted() is True


def test_paper_mode_is_not_gated_by_reconciliation(tmp_path):
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    result = reconcile_on_start(store, live=False, grace_seconds=0.0)
    assert result["checked"] is False and is_halted() is False


def test_recent_claims_are_within_the_grace_period(tmp_path):
    store = OrderKeyStore(f"sqlite:///{tmp_path / 'k.sqlite'}")
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    assert find_unreconciled(store, grace_seconds=300.0) == []


# ---- the triple gate -----------------------------------------------------


def _broker(live_enabled: bool):
    from intradyne.engine.broker_ccxt import CCXTBroker

    return CCXTBroker("bitget", "k", "s", "p", live_enabled)


def test_live_broker_refuses_while_halted():
    """The halt must hold at the boundary that actually spends money, not
    only at the gate upstream of it."""
    set_halt(True, reason="manual stop")
    with pytest.raises(ComplianceError, match="halted"):
        asyncio.run(_broker(True).place_order("BTC/USDT", "buy", "market", 1.0, None))


def test_live_broker_refuses_when_live_is_disabled():
    with pytest.raises(ComplianceError, match="Live trading disabled"):
        asyncio.run(_broker(False).place_order("BTC/USDT", "buy", "market", 1.0, None))


def test_live_broker_refuses_leveraged_parameters():
    with pytest.raises(ComplianceError):
        asyncio.run(
            _broker(True).place_order(
                "BTC/USDT", "buy", "market", 1.0, None, {"leverage": 3}
            )
        )


# ---- the gate itself stays shut -----------------------------------------


def test_the_live_gate_remains_closed():
    """Phase 5 builds the controls; opening the gate is a separate, human
    decision requiring a testnet soak and verified alerting."""
    from intradyne.core.config import LIVE_TRADING_GATE_OPEN

    assert LIVE_TRADING_GATE_OPEN is False
