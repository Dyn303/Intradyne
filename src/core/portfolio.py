from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class Portfolio:
    cash: float
    max_drawdown: float
    positions: Dict[str, float] = field(default_factory=dict)  # qty per symbol
    equity: float = 0.0
    start_of_day_equity: float = 0.0
    max_equity_today: float = 0.0
    min_equity_today: float = 0.0
    trades_today: int = 0
    daily_returns: List[float] = field(default_factory=list)

    def update_equity(self, prices: Dict[str, float]) -> None:
        pos_value = 0.0
        for sym, qty in self.positions.items():
            pos_value += qty * prices.get(sym, 0.0)
        self.equity = self.cash + pos_value
        if self.start_of_day_equity == 0.0:
            self.start_of_day_equity = self.equity
            self.max_equity_today = self.equity
            self.min_equity_today = self.equity
        self.max_equity_today = max(self.max_equity_today, self.equity)
        self.min_equity_today = min(self.min_equity_today, self.equity)

    def current_drawdown(self) -> float:
        if self.max_equity_today <= 0:
            return 0.0
        return max(0.0, (self.max_equity_today - self.equity) / self.max_equity_today)

    def check_risk_limits(self) -> bool:
        return self.current_drawdown() >= self.max_drawdown

    def rebalance_to_targets(
        self,
        target_weights: Dict[str, float],
        prices: Dict[str, float],
        fee_bps: float = 10.0,
        slip_bps: float = 10.0,
        skip_threshold: float = 0.005,  # 0.5%
    ) -> List[Tuple[str, str, float, float]]:
        # Ensure cash symbol exists
        targets = {**target_weights}
        targets.setdefault("USDT", 1.0 - sum(v for k, v in targets.items() if k != "USDT"))
        # Compute current holdings value
        holdings_value = sum(self.positions.get(sym, 0.0) * prices.get(sym, 0.0) for sym in self.positions)
        total_equity = self.cash + holdings_value
        if total_equity <= 0:
            return []

        trades: List[Tuple[str, str, float, float]] = []
        # Target value per symbol
        for sym, w in targets.items():
            if sym == "USDT":
                continue
            px = prices.get(sym)
            if not px or px <= 0:
                continue
            target_value = total_equity * max(0.0, min(1.0, w))
            current_value = self.positions.get(sym, 0.0) * px
            delta_value = target_value - current_value
            # Skip small adjustments
            if abs(delta_value) < skip_threshold * total_equity:
                continue
            side = "buy" if delta_value > 0 else "sell"
            # Apply slippage to fill price
            fill_px = px * (1.0 + (slip_bps / 10000.0) * (1 if side == "buy" else -1))
            qty = abs(delta_value) / fill_px
            # Fees in quote (USDT)
            fee = (abs(delta_value) * fee_bps) / 10000.0
            if side == "buy":
                cost = qty * fill_px + fee
                if self.cash >= cost:
                    self.cash -= cost
                    self.positions[sym] = self.positions.get(sym, 0.0) + qty
                    trades.append((sym, side, qty, fill_px))
                    self.trades_today += 1
            else:
                # sell up to available qty
                sell_qty = min(qty, self.positions.get(sym, 0.0))
                if sell_qty > 0:
                    proceeds = sell_qty * fill_px - fee
                    self.positions[sym] = self.positions.get(sym, 0.0) - sell_qty
                    if self.positions[sym] <= 0:
                        self.positions.pop(sym, None)
                    self.cash += max(0.0, proceeds)
                    trades.append((sym, side, sell_qty, fill_px))
                    self.trades_today += 1
        return trades

