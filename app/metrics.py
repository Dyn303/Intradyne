from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    start_ts: float = field(default_factory=time.time)
    trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: float = 0.0
    max_drawdown: float = 0.0

    def as_prometheus(self) -> str:
        lines = [
            f"intradyne_trades_total {self.trades}",
            f"intradyne_wins_total {self.wins}",
            f"intradyne_losses_total {self.losses}",
            f"intradyne_realized_pnl {self.realized_pnl}",
            f"intradyne_max_drawdown {self.max_drawdown}",
        ]
        return "\n".join(lines) + "\n"


METRICS = Metrics()

