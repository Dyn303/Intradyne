from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from intradyne.api.deps import get_ledger
from src.core.ai import (
    AIUnavailable,
    ai_configured,
    summarize_guardrails_async,
    explain_decision_async,
)
from intradyne.api.ratelimit import ai_rate_limit
from intradyne.api.deps import get_guardrails
from src.risk.guardrails import OrderReq


router = APIRouter(dependencies=[Depends(ai_rate_limit)])


@router.get("/ai/status")
async def ai_status() -> Dict[str, Any]:
    return {"configured": ai_configured()}


@router.post("/ai/summarize")
async def ai_summarize(n: int = 100) -> Dict[str, Any]:
    if not ai_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    # Load last N records from the explainability ledger
    led = get_ledger()
    path = led.path
    buf: List[Dict[str, Any]] = []
    try:
        import orjson  # lazy import

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = orjson.loads(line)
                except Exception:
                    continue
                buf.append(rec)
    except FileNotFoundError:
        buf = []

    try:
        summary = await summarize_guardrails_async(buf[-max(0, int(n)) :])
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"AI error: {e}")
    return {"summary": summary}


@router.post("/ai/explain")
async def ai_explain(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Explain guardrail decision for a proposed order. Always returns a
    structured explanation; if AI is configured, includes `nl` text.
    payload: { symbol: str, side: str, qty: float, meta?: dict, prefer_ai?: bool }
    """
    symbol = str(payload.get("symbol", "")).strip()
    side = str(payload.get("side", "")).strip().lower()
    qty = float(payload.get("qty", 0.0) or 0.0)
    meta: Optional[Dict[str, Any]] = payload.get("meta") or {}
    prefer_ai = bool(payload.get("prefer_ai", True))
    if not symbol or side not in {"buy", "sell"} or qty <= 0:
        raise HTTPException(status_code=400, detail="Invalid order payload")

    gr = get_guardrails()
    req = OrderReq(symbol=symbol, side=side, qty=qty, meta=meta)
    decision, reasons, final_req = gr.gate_trade(req)
    base: Dict[str, Any] = {
        "decision": decision,
        "reasons": reasons,
        "requested_order": {"symbol": symbol, "side": side, "qty": qty, "meta": meta},
        "final_order": {
            "symbol": final_req.symbol,
            "side": final_req.side,
            "qty": final_req.qty,
            "meta": final_req.meta,
        },
    }
    if prefer_ai and ai_configured():
        try:
            nl = await explain_decision_async(
                decision=decision,
                reasons=reasons,
                requested=base["requested_order"],
                final=base["final_order"],
            )
            base["nl"] = nl
        except Exception:
            # Fallback silently if AI fails
            pass
    return base
