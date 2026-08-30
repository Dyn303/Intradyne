"""The single order path.

POST /orders used to gate an order and then fabricate a UUID: it returned
{"status": "accepted"} without contacting any broker and without the portfolio
moving. These tests pin the real behaviour -- an order that is allowed really
fills, and one that is refused never reaches a broker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import httpx
import pytest

from intradyne.api import deps
from intradyne.core.config import reset_settings_cache
from intradyne.risk.kill_switch import set_halt


@pytest.fixture
def api(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """A fresh app with an isolated ledger and a clean portfolio."""
    for k in ("APP_ENV", "API_AUTH_REQUIRED", "X_API_KEY", "FRONTEND_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EXPLAIN_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("ALLOWED_SYMBOLS", "BTC,ETH")
    reset_settings_cache()
    deps.reset_execution_manager()
    set_halt(False)

    from intradyne.api.app import create_app

    app = create_app()
    yield app

    set_halt(False)
    deps.reset_execution_manager()
    reset_settings_cache()


def _post(app, path: str, body: Dict[str, Any]) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(path, json=body)

    return asyncio.run(go())


def _get(app, path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get(path)

    return asyncio.run(go())


def _portfolio():
    return deps.get_execution_manager().ctx.portfolio


# ---- an allowed order really executes ------------------------------------


def test_buy_actually_moves_the_portfolio(api):
    before = _portfolio().balances["USDT"]

    r = _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "buy", "qty": 0.01, "price": 50_000},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "filled"

    pf = _portfolio()
    assert pf.get_position("BTC/USDT").base == pytest.approx(0.01)
    # 0.01 @ 50k = 500, plus slippage and taker fee.
    assert pf.balances["USDT"] < before - 500


def test_fill_is_recorded_in_the_ledger_and_the_chain_verifies(api):
    _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "buy", "qty": 0.01, "price": 50_000},
    )

    ledger = deps.get_guardrails().ledger
    events = [r.get("event") for r in ledger.iter_all()]
    assert "order_filled" in events
    assert ledger.verify_chain()[0] is True


def test_portfolio_endpoint_reflects_the_same_portfolio(api):
    _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "buy", "qty": 0.02, "price": 40_000},
    )
    body = _get(api, "/portfolio").json()
    assert body["positions"]["BTC/USDT"]["base"] == pytest.approx(0.02)


def test_buy_then_sell_round_trip(api):
    _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "buy", "qty": 0.02, "price": 40_000},
    )
    r = _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "sell", "qty": 0.02, "price": 41_000},
    )
    assert r.status_code == 200, r.text
    assert _portfolio().get_position("BTC/USDT").base == pytest.approx(0.0)


# ---- a refused order never reaches a broker ------------------------------


def _assert_nothing_executed(app, body):
    before_cash = _portfolio().balances["USDT"]
    r = _post(app, "/orders", body)
    assert r.status_code == 400, r.text
    assert _portfolio().balances["USDT"] == before_cash
    assert _portfolio().get_position(body["symbol"]).base == 0.0
    return r


def test_symbol_outside_the_whitelist_is_refused(api):
    r = _assert_nothing_executed(
        api, {"symbol": "DOGE/USDT", "side": "buy", "qty": 1, "price": 0.1}
    )
    assert "not in allowed list" in str(r.json()["detail"]["reasons"])


def test_sell_without_inventory_is_refused(api):
    """Long-only: you may not sell what you do not own."""
    r = _assert_nothing_executed(
        api, {"symbol": "BTC/USDT", "side": "sell", "qty": 1, "price": 50_000}
    )
    assert "short selling" in str(r.json()["detail"]["reasons"]).lower()


def test_selling_more_than_held_is_refused(api):
    """Regression: forbid_shorting only rejected sells from a flat position,
    so selling 10 while holding 1 passed the compliance check. Only the paper
    broker's clamp hid it; the live path would have shorted the difference."""
    _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "buy", "qty": 0.01, "price": 50_000},
    )
    r = _post(
        api,
        "/orders",
        {"symbol": "BTC/USDT", "side": "sell", "qty": 5.0, "price": 50_000},
    )
    assert r.status_code == 400, r.text
    assert "exceeds inventory" in str(r.json()["detail"]["reasons"])
    # the original position is untouched
    assert _portfolio().get_position("BTC/USDT").base == pytest.approx(0.01)


def test_operator_halt_blocks_api_orders(api):
    set_halt(True, reason="manual stop")
    r = _assert_nothing_executed(
        api, {"symbol": "BTC/USDT", "side": "buy", "qty": 0.01, "price": 50_000}
    )
    assert r.json()["detail"]["error"] == "halt"


def test_refusal_is_recorded_in_the_ledger(api):
    _post(
        api, "/orders", {"symbol": "DOGE/USDT", "side": "buy", "qty": 1, "price": 0.1}
    )
    ledger = deps.get_guardrails().ledger
    events = [r.get("event") for r in ledger.iter_all()]
    assert "order_blocked" in events
    assert ledger.verify_chain()[0] is True


# ---- input validation ----------------------------------------------------


def test_market_order_without_a_mark_is_rejected(api):
    r = _post(api, "/orders", {"symbol": "BTC/USDT", "side": "buy", "qty": 0.01})
    assert r.status_code == 400
    assert "price_required" in str(r.json()["detail"])


@pytest.mark.parametrize("qty", [0, -1])
def test_non_positive_quantity_is_rejected(api, qty):
    r = _post(
        api, "/orders", {"symbol": "BTC/USDT", "side": "buy", "qty": qty, "price": 100}
    )
    assert r.status_code == 422


def test_unknown_side_is_rejected(api):
    r = _post(
        api, "/orders", {"symbol": "BTC/USDT", "side": "short", "qty": 1, "price": 100}
    )
    assert r.status_code == 400


# ---- gate-level checks the HTTP surface cannot express -------------------


def _gate(tmp_path):
    from datetime import datetime
    from typing import List, Optional, Tuple

    from intradyne.core.ledger import Ledger
    from intradyne.risk.guardrails import Guardrails, PriceFeed, RiskData
    from intradyne.risk.shariah import ShariahPolicy

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
        ledger=Ledger(path=str(tmp_path / "g.jsonl")),
        shariah=ShariahPolicy(allowed_crypto=["BTC/USDT"]),
    )


@pytest.mark.parametrize(
    "param", ["leverage", "marginMode", "reduceOnly", "futures", "swap", "contract"]
)
def test_leveraged_or_derivative_params_are_refused(tmp_path, param):
    """No riba, no gharar: margin, leverage and derivatives are barred."""
    from intradyne.risk.guardrails import OrderReq

    gr = _gate(tmp_path)
    action, reasons, _ = gr.gate_trade(
        OrderReq(symbol="BTC/USDT", side="buy", qty=1.0, params={param: 5})
    )
    assert action == "block"
    assert param in reasons[0]


def test_blocked_business_tags_are_refused(tmp_path):
    """Business screening: prohibited underlying activity."""
    from intradyne.risk.guardrails import OrderReq

    gr = _gate(tmp_path)
    action, reasons, _ = gr.gate_trade(
        OrderReq(symbol="BTC/USDT", side="buy", qty=1.0, meta={"tags": ["gambling"]})
    )
    assert action == "block"
    assert "gambling" in reasons[0]


def test_sell_with_unknown_inventory_fails_closed(tmp_path):
    """Without inventory a sell cannot be shown to be covered, and an
    uncovered sell is exactly what is prohibited."""
    from intradyne.risk.guardrails import OrderReq

    gr = _gate(tmp_path)
    action, reasons, _ = gr.gate_trade(
        OrderReq(symbol="BTC/USDT", side="sell", qty=1.0, base_inventory=None)
    )
    assert action == "block"
    assert "inventory unknown" in reasons[0]
