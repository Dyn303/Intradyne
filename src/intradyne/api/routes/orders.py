from __future__ import annotations

import uuid
from typing import Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from intradyne.risk.guardrails import Guardrails, OrderReq
from intradyne.api.deps import get_guardrails


router = APIRouter()


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


@router.post("/orders")
def create_order(inp: OrderIn):
    gr = get_guardrails()

    def _exec(o: OrderReq) -> Dict:
        return {"trade_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4()), "status": "accepted"}

    ok, payload = submit_order(gr, OrderReq(symbol=inp.symbol, side=inp.side, qty=inp.qty), _exec)
    if not ok:
        raise HTTPException(status_code=400, detail=payload)
    return payload
