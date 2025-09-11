from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger

from .portfolio import Portfolio


_id_counter = itertools.count(1)


@dataclass
class Order:
    id: str
    symbol: str
    side: str  # buy/sell
    type: str  # market/limit
    qty: float
    price: Optional[float] = None
    filled: float = 0.0
    status: str = "open"  # open, filled, canceled, partial


class PaperBroker:
    def __init__(self, portfolio: Portfolio, slippage_bps: int = 2) -> None:
        self.portfolio = portfolio
        self.slippage_bps = slippage_bps
        self.orders: Dict[str, Order] = {}

    def _new_order_id(self) -> str:
        return f"PAPER-{next(_id_counter)}"

    def _apply_slippage(self, price: float, side: str) -> float:
        bps = self.slippage_bps / 10_000.0
        if side == "buy":
            return price * (1.0 + bps)
        else:
            return price * (1.0 - bps)

    def place_order(self, symbol: str, side: str, type_: str, qty: float, price: Optional[float], l1: Dict[str, float]) -> Order:
        oid = self._new_order_id()
        order = Order(id=oid, symbol=symbol, side=side, type=type_, qty=qty, price=price)
        self.orders[oid] = order
        self._try_fill(order, l1)
        return order

    def cancel(self, order_id: str) -> None:
        order = self.orders.get(order_id)
        if order and order.status == "open":
            order.status = "canceled"

    def _try_fill(self, order: Order, l1: Dict[str, float]) -> None:
        if order.status not in ("open", "partial"):
            return
        bid = l1.get("bid") or l1.get("last")
        ask = l1.get("ask") or l1.get("last")
        if order.type == "market":
            px = ask if order.side == "buy" else bid
            px_slip = self._apply_slippage(px, order.side)
            self._execute(order, order.qty, px_slip, is_maker=False)
            order.status = "filled"
            order.filled = order.qty
        elif order.type == "limit":
            if order.side == "buy" and ask is not None and order.price is not None and ask <= order.price:
                self._execute(order, order.qty, order.price, is_maker=True)
                order.status = "filled"
                order.filled = order.qty
            elif order.side == "sell" and bid is not None and order.price is not None and bid >= order.price:
                self._execute(order, order.qty, order.price, is_maker=True)
                order.status = "filled"
                order.filled = order.qty
            else:
                # leave open, could implement partials with probability, but keep deterministic
                pass

    def _execute(self, order: Order, qty: float, price: float, is_maker: bool) -> None:
        logger.bind(event="paper_fill").info({"order_id": order.id, "symbol": order.symbol, "side": order.side, "qty": qty, "price": price, "maker": is_maker})
        if order.side == "buy":
            self.portfolio.buy(order.symbol, qty, price, is_maker=is_maker)
        else:
            self.portfolio.sell(order.symbol, qty, price, is_maker=is_maker)

