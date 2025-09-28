from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Position:
    symbol: str
    base: float = 0.0  # quantity of base asset
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def update_on_buy(self, qty: float, price: float) -> None:
        if qty <= 0:
            return
        notional_new = qty * price
        notional_old = self.base * self.avg_price
        total_base = self.base + qty
        self.avg_price = (
            (notional_old + notional_new) / total_base if total_base > 0 else 0.0
        )
        self.base = total_base

    def update_on_sell(self, qty: float, price: float) -> float:
        if qty <= 0:
            return 0.0
        qty = min(qty, self.base)
        pnl = (price - self.avg_price) * qty
        self.base -= qty
        if self.base == 0:
            self.avg_price = 0.0
        self.realized_pnl += pnl
        return pnl


@dataclass
class Portfolio:
    quote_ccy: str = "USDT"
    balances: Dict[str, float] = field(default_factory=lambda: {"USDT": 10_000.0})
    positions: Dict[str, Position] = field(default_factory=dict)
    maker_bps: int = 2
    taker_bps: int = 5

    def equity(self, marks: Optional[Dict[str, float]] = None) -> float:
        eq = self.balances.get(self.quote_ccy, 0.0)
        if marks:
            for sym, pos in self.positions.items():
                if pos.base > 0:
                    px = marks.get(sym, pos.avg_price)
                    eq += pos.base * px
        return eq

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def fee_for(self, notional: float, is_maker: bool) -> float:
        bps = self.maker_bps if is_maker else self.taker_bps
        return notional * (bps / 10_000.0)

    def buy(
        self, symbol: str, qty: float, price: float, is_maker: bool = False
    ) -> None:
        notional = qty * price
        fee = self.fee_for(notional, is_maker)
        total_cost = notional + fee
        if self.balances.get(self.quote_ccy, 0.0) < total_cost:
            raise ValueError("Insufficient quote balance for buy")
        self.balances[self.quote_ccy] -= total_cost
        pos = self.get_position(symbol)
        pos.update_on_buy(qty, price)

    def sell(
        self, symbol: str, qty: float, price: float, is_maker: bool = False
    ) -> float:
        pos = self.get_position(symbol)
        if qty > pos.base:
            qty = pos.base
        notional = qty * price
        fee = self.fee_for(notional, is_maker)
        pnl = pos.update_on_sell(qty, price)
        self.balances[self.quote_ccy] += notional - fee
        return pnl
