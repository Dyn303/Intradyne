from __future__ import annotations

import time
from math import isfinite
from typing import Any, Callable, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from intradyne.risk.guardrails import Guardrails, OrderReq
from intradyne.api.deps import get_execution_manager, get_guardrails
from intradyne.core.config import load_settings


router = APIRouter()


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: float = Field(gt=0)
    #: Limit price, or an optional manual mark for a paper market order.
    price: Optional[float] = Field(default=None, gt=0)
    type: str = "market"


@router.get("/market/quote")
async def market_quote(symbol: str = Query(max_length=32)) -> Dict[str, Any]:
    """Read a fresh public Bitget spot quote for an allowlisted pair."""
    pair = symbol.strip().upper()
    if pair not in load_settings().allowed_crypto_list():
        raise HTTPException(status_code=400, detail="symbol is not on the whitelist")
    venue_symbol = pair.replace("/", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://api.bitget.com/api/v3/market/tickers",
                params={"category": "SPOT", "symbol": venue_symbol},
            )
            response.raise_for_status()
        payload = response.json()
        if payload["code"] != "00000":
            raise ValueError("provider rejected quote")
        quote = payload["data"][0]
        if quote["symbol"] != venue_symbol or quote["category"] != "SPOT":
            raise ValueError("unexpected venue instrument")
        bid, ask, last = (
            float(quote[field]) for field in ("bid1Price", "ask1Price", "lastPrice")
        )
        ts = float(quote["ts"]) / 1000
        if not all(isfinite(value) and value > 0 for value in (bid, ask, last, ts)):
            raise ValueError("invalid quote values")
        if bid > ask or not -5 <= time.time() - ts <= 30:
            raise ValueError("crossed or stale quote")
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(
            status_code=503, detail="fresh Bitget spot quote unavailable"
        ) from exc
    return {
        "symbol": pair,
        "source": "bitget_spot",
        "bid": bid,
        "ask": ask,
        "last": last,
        "ts": ts,
    }


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
    if inp.price is None and (type_ == "limit" or load_settings().mode != "paper"):
        raise HTTPException(
            status_code=400,
            detail=(
                "price_required: supply a limit price or a mark for non-paper orders."
            ),
        )

    execution = get_execution_manager()
    if inp.price is None:
        l1 = await market_quote(inp.symbol)
    else:
        mark = float(inp.price)
        l1 = {"bid": mark, "ask": mark, "last": mark, "ts": time.time()}

    result = await execution.submit(
        symbol=l1.get("symbol", inp.symbol),
        side=side,
        type_=type_,
        qty=inp.qty,
        price=inp.price if type_ == "limit" else None,
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
