from __future__ import annotations

from typing import Iterable, List

from src.core.types import Order, VenueQuote, ChildOrder


class SmartOrderRouter:
    async def route_order(
        self, order: Order, venues: Iterable[VenueQuote]
    ) -> List[ChildOrder]:
        # Naive: route entire qty to best price
        best = None
        for v in venues:
            if best is None:
                best = v
                continue
            if order.side.lower() == "buy":
                if v.price < best.price:
                    best = v
            else:
                if v.price > best.price:
                    best = v
        if not best:
            return []
        qty = min(order.qty, best.available)
        return [
            ChildOrder(
                venue=best.venue,
                symbol=order.symbol,
                side=order.side,
                qty=qty,
                price=best.price,
            )
        ]


__all__ = ["SmartOrderRouter"]
