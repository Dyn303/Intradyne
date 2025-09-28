from __future__ import annotations

import time
from dataclasses import dataclass, field
from collections import deque
from typing import Deque, List


@dataclass
class Metrics:
    start_ts: float = field(default_factory=time.time)
    trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: float = 0.0
    max_drawdown: float = 0.0
    current_equity: float = 0.0
    # Rolling samples for MFE/MAE percentiles (pct returns)
    _mfe_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    _mae_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))

    def record_mfe_mae(self, mfe_pct: float, mae_pct: float) -> None:
        try:
            self._mfe_samples.append(float(mfe_pct))
            self._mae_samples.append(float(mae_pct))
        except Exception:
            pass

    def update_equity(self, equity: float) -> None:
        try:
            self.current_equity = float(equity)
        except Exception:
            pass

    @staticmethod
    def _percentile(samples: List[float], q: float) -> float:
        if not samples:
            return 0.0
        xs = sorted(samples)
        q = max(0.0, min(1.0, q))
        idx = int(round(q * (len(xs) - 1)))
        return float(xs[idx])

    def as_prometheus(self) -> str:
        # Percentiles for MFE/MAE (p50 and p90)
        mfe_list = list(self._mfe_samples)
        mae_list = list(self._mae_samples)
        mfe_p50 = self._percentile(mfe_list, 0.50)
        mfe_p90 = self._percentile(mfe_list, 0.90)
        mae_p50 = self._percentile(mae_list, 0.50)
        mae_p90 = self._percentile(mae_list, 0.90)

        lines = [
            f"intradyne_trades_total {self.trades}",
            f"intradyne_wins_total {self.wins}",
            f"intradyne_losses_total {self.losses}",
            f"intradyne_realized_pnl {self.realized_pnl}",
            f"intradyne_max_drawdown {self.max_drawdown}",
            f"intradyne_equity {self.current_equity}",
            f"intradyne_mfe_pct_p50 {mfe_p50}",
            f"intradyne_mfe_pct_p90 {mfe_p90}",
            f"intradyne_mae_pct_p50 {mae_p50}",
            f"intradyne_mae_pct_p90 {mae_p90}",
        ]
        return "\n".join(lines) + "\n"


METRICS = Metrics()
