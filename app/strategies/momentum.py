from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class MomentumState:
    prices: Deque[float] = field(default_factory=lambda: deque(maxlen=120))  # 2m at 1s


@dataclass
class MomentumStrategy:
    symbol: str
    breakout_window: int = 60  # seconds
    min_range_bps: int = 5
    state: MomentumState = field(default_factory=MomentumState)
    id: str = "mom_scalper_v1"

    def on_tick(self, l1: Dict[str, float]) -> Optional[Dict[str, object]]:
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            return None
        self.state.prices.append(float(last))
        if len(self.state.prices) < self.breakout_window:
            return None
        window = list(self.state.prices)[-self.breakout_window:]
        pmax = max(window)
        pmin = min(window)
        if pmin <= 0:
            return None
        range_bps = (pmax - pmin) / pmin * 10_000
        # breakout: current near highs with some range
        if range_bps >= self.min_range_bps and last >= pmax:
            return {
                "action": "buy",
                "reason": "breakout",
                "features": {"range_bps": range_bps},
            }
        return None

