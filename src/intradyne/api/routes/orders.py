from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from intradyne.risk.guardrails import Guardrails, OrderReq
from intradyne.api.deps import get_execution_manager, get_guardrails


router = APIRouter()


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: float = Field(gt=0)
    #: Limit price, or the mark to fill a market order against. Required
    #: while the engine loop is not running, since there is then no live mark
    #: to price a market order from.
    price: Optional[float] = Field(default=None, gt=0)
    type: str = "market"


def submit_order(
    guardrails: Guardrails,
    order: OrderReq,
    executor: Callable[[OrderReq], Dict],
) -> Tuple[bool, Dict]:
    """Gate an order, then hand the approved form to `executor`.

    Retained for callers that supply their own executor. The operator halt is
    enforced inside gate_trade, so it covers every caller rather than only
    this route.
    """
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
            "exec": {
                k: result.get(k) for k in ("order_id", "status", "venue") if k in result
            },
        },
    )
    return True, result


@router.post("/orders")
async def create_order(inp: OrderIn) -> Dict[str, Any]:
    """Submit an order.

    This used to gate the order and then fabricate a UUID, returning
    {"status": "accepted"} without any broker having been contacted and
    without the portfolio moving. It now goes through the same
    ExecutionManager the engine uses, so the order is really filled against
    the paper broker, the portfolio really moves, and both the decision and
    the fill land in the explainability ledger.
    """
    side = inp.side.strip().lower()
    if side not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail="side must be buy or sell")
    type_ = inp.type.strip().lower()
    if type_ not in {"market", "limit"}:
        raise HTTPException(status_code=400, detail="type must be market or limit")
    if inp.price is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "price_required: no live mark is available to fill against. "
                "Supply the limit price, or the mark for a market order."
            ),
        )

    execution = get_execution_manager()
    mark = float(inp.price)
    l1 = {"bid": mark, "ask": mark, "last": mark, "ts": time.time()}

    result = await execution.submit(
        symbol=inp.symbol,
        side=side,
        type_=type_,
        qty=inp.qty,
        price=mark if type_ == "limit" else None,
        l1=l1,
        strategy_id="api",
        features={},
        checks_passed={},
    )

    if result.get("status") == "blocked":
        raise HTTPException(
            status_code=400,
            detail={"error": result.get("action"), "reasons": result.get("reasons")},
        )
    return dict(result)


@router.get("/portfolio")
def get_portfolio_state() -> Dict[str, Any]:
    """Balances and open positions, from the same portfolio orders move."""
    portfolio = get_execution_manager().ctx.portfolio
    return {
        "quote_ccy": portfolio.quote_ccy,
        "balances": dict(portfolio.balances),
        "positions": {
            symbol: {
                "base": pos.base,
                "avg_price": pos.avg_price,
                "realized_pnl": pos.realized_pnl,
            }
            for symbol, pos in portfolio.positions.items()
            if pos.base > 0 or pos.realized_pnl
        },
    }


__all__ = ["router", "submit_order", "get_guardrails"]
