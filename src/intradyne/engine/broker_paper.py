from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

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
    status: str = "open"  # open, filled, canceled, partial, expired
    #: When the order was placed, so resting limits can be aged out.
    created_ts: Optional[float] = None
    #: True when the fill earned the maker rebate rather than paying taker.
    filled_as_maker: bool = False


class PaperBroker:
    def __init__(
        self,
        portfolio: Portfolio,
        slippage_bps: int = 2,
        limit_ttl_s: float = 60.0,
    ) -> None:
        self.portfolio = portfolio
        self.slippage_bps = slippage_bps
        #: How long a resting limit order waits before being cancelled. A
        #: passive order that never fills must eventually go away, or the
        #: book fills with stale intentions.
        self.limit_ttl_s = float(limit_ttl_s)
        self.orders: Dict[str, Order] = {}
        #: Maker orders that rested to their TTL without filling. Each is a
        #: trade the strategy wanted and did not get.
        self.expired = 0

    def _new_order_id(self) -> str:
        return f"PAPER-{next(_id_counter)}"

    def _apply_slippage(self, price: float, side: str) -> float:
        bps = self.slippage_bps / 10_000.0
        if side == "buy":
            return price * (1.0 + bps)
        else:
            return price * (1.0 - bps)

    def place_order(
        self,
        symbol: str,
        side: str,
        type_: str,
        qty: float,
        price: Optional[float],
        l1: Mapping[str, Any],
    ) -> Order:
        oid = self._new_order_id()
        order = Order(
            id=oid,
            symbol=symbol,
            side=side,
            type=type_,
            qty=qty,
            price=price,
            created_ts=l1.get("ts"),
        )
        self.orders[oid] = order
        self._try_fill(order, l1, at_submission=True)
        return order

    def on_tick(self, l1: Mapping[str, Any]) -> None:
        """Re-evaluate resting limit orders against a new quote.

        Without this, `_try_fill` ran only at submission: a passive order that
        did not fill immediately stayed open forever and could never execute,
        which made maker-style execution impossible to model at all.
        """
        symbol = l1.get("symbol")
        now = l1.get("ts")
        for order in list(self.orders.values()):
            if order.status != "open" or order.symbol != symbol:
                continue
            if (
                now is not None
                and order.created_ts is not None
                and (float(now) - float(order.created_ts)) > self.limit_ttl_s
            ):
                # An expiring maker order is a *missed trade*, not a saving.
                # This set the status and moved on, leaving no trace anywhere,
                # so the one number that decides whether maker execution is
                # worth its lower fee -- the fill rate -- could not be counted
                # at all. Same class as the silent drops fixed in #50 and #54.
                order.status = "expired"
                self.expired += 1
                logger.bind(event="order_expired").info(
                    {
                        "order_id": order.id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "qty": order.qty,
                        "price": order.price,
                        "resting_s": round(float(now) - float(order.created_ts), 2),
                    }
                )
                continue
            self._try_fill(order, l1)

    def open_orders(self, symbol: Optional[str] = None) -> Dict[str, Order]:
        return {
            oid: o
            for oid, o in self.orders.items()
            if o.status == "open" and (symbol is None or o.symbol == symbol)
        }

    def cancel(self, order_id: str) -> None:
        order = self.orders.get(order_id)
        if order and order.status == "open":
            order.status = "canceled"

    def _try_fill(
        self, order: Order, l1: Mapping[str, Any], at_submission: bool = False
    ) -> None:
        if order.status not in ("open", "partial"):
            return
        bid = l1.get("bid") or l1.get("last")
        ask = l1.get("ask") or l1.get("last")

        if order.type == "market":
            px = ask if order.side == "buy" else bid
            if px is None:
                # A quote carrying neither side nor a last price cannot fill
                # anything. Passing None through reached _apply_slippage and
                # raised on the multiply, taking the tick loop down.
                return
            px_slip = self._apply_slippage(px, order.side)
            self._execute(order, order.qty, px_slip, is_maker=False)
            order.status = "filled"
            order.filled = order.qty
            return

        if order.type != "limit" or order.price is None:
            return

        crosses = (order.side == "buy" and ask is not None and ask <= order.price) or (
            order.side == "sell" and bid is not None and bid >= order.price
        )
        if not crosses:
            return  # rests in the book; on_tick will look again

        if at_submission:
            # A limit that is already marketable takes liquidity: it crosses
            # the spread and pays the taker fee at the touch. Booking it as a
            # maker fill at the limit price -- as this did -- credits a rebate
            # that was never earned and makes maker execution look free.
            touch = ask if order.side == "buy" else bid
            if touch is None:
                return
            self._execute(
                order,
                order.qty,
                self._apply_slippage(touch, order.side),
                is_maker=False,
            )
        else:
            # It rested and the market came to it: a genuine maker fill at the
            # posted price, with no spread paid.
            self._execute(order, order.qty, order.price, is_maker=True)
        order.status = "filled"
        order.filled = order.qty

    def _execute(self, order: Order, qty: float, price: float, is_maker: bool) -> None:
        order.filled_as_maker = is_maker
        logger.bind(event="paper_fill").info(
            {
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": qty,
                "price": price,
                "maker": is_maker,
            }
        )
        if order.side == "buy":
            self.portfolio.buy(order.symbol, qty, price, is_maker=is_maker)
        else:
            self.portfolio.sell(order.symbol, qty, price, is_maker=is_maker)
