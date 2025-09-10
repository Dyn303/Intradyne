from __future__ import annotations

import uuid
from typing import Callable, Dict, Tuple, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.risk.guardrails import Guardrails, OrderReq, ShariahPolicy, PriceFeed, RiskData
from src.core.ledger import Ledger
from src.config import load_settings


class _DefaultPriceFeed(PriceFeed):
    def get_price(self, symbol: str, at: Optional[str] = None) -> Optional[float]:
        return None


class _DefaultRiskData(RiskData):
    def equity_series_30d(self):
        return []

    def equity_daily_returns_30d(self):
        return []


def _build_guardrails(allowed_crypto: Optional[list[str]] = None) -> Guardrails:
    settings = load_settings()
    sh = ShariahPolicy(allowed_crypto=allowed_crypto or [])
    return Guardrails(price_feed=_DefaultPriceFeed(), risk_data=_DefaultRiskData(), ledger=Ledger(path=load_settings().EXPLAIN_LEDGER_PATH), shariah=sh,
                      thresholds={
                          "dd_warn": settings.DD_WARN_PCT,
                          "dd_halt": settings.DD_HALT_PCT,
                          "flash": settings.FLASH_CRASH_PCT,
                          "kill_switch": settings.KILL_SWITCH_BREACHES,
                          "var_max": settings.VAR_1D_MAX,
                      })


_engine: Optional[Guardrails] = None


def get_engine() -> Guardrails:
    global _engine
    if _engine is None:
        # Try to read from config.yaml (optional) for allowed crypto via intradyne if available
        allowed = []
        try:
            from intradyne_lite.core.config import load_config  # type: ignore
            cfg = load_config("config.yaml")
            sh = (cfg.get("shariah") or {})
            allowed = load_settings().allowed_crypto_list() or list(((sh.get("crypto") or {}).get("allowed", [])))
        except Exception:
            pass
        _engine = _build_guardrails(allowed)
    return _engine


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: float


def submit_order(
    guardrails: Guardrails,
    order: OrderReq,
    executor: Callable[[OrderReq], Dict],
) -> Tuple[bool, Dict]:
    action, reasons, adj = guardrails.gate_trade(order)
    if action != "allow":
        guardrails.ledger.append(
            "order_blocked",
            {
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "action": action,
                "reasons": reasons,
            },
        )
        return False, {"error": action, "reasons": reasons}

    result = executor(adj)
    guardrails.ledger.append(
        "order_allowed",
        {
            "symbol": adj.symbol,
            "side": adj.side,
            "qty": adj.qty,
            "reasons": reasons,
            "exec": {k: result.get(k) for k in ("order_id", "status", "venue") if k in result},
        },
    )
    return True, result


def create_app(engine: Optional[Guardrails] = None) -> FastAPI:
    app = FastAPI(title="Orders API")
    app.state.guardrails = engine or get_engine()

    @app.post("/orders")
    def create_order(inp: OrderIn):
        gr: Guardrails = app.state.guardrails

        def _exec(o: OrderReq) -> Dict:
            return {"trade_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4()), "status": "accepted"}

        ok, payload = submit_order(gr, OrderReq(symbol=inp.symbol, side=inp.side, qty=inp.qty), _exec)
        if not ok:
            raise HTTPException(status_code=400, detail=payload)
        return payload

    return app


# default app for convenience
app = create_app()



