from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple


def bollinger(prices: Deque[float], window: int = 60, k: float = 2.0) -> Optional[Tuple[float, float, float]]:
    if len(prices) < window:
        return None
    w = list(prices)[-window:]
    mean = sum(w) / window
    var = sum((x - mean) ** 2 for x in w) / window
    std = var ** 0.5
    return mean, mean - k * std, mean + k * std


@dataclass
class MeanRevState:
    prices: Deque[float] = field(default_factory=lambda: deque(maxlen=300))


@dataclass
class MeanRevStrategy:
    symbol: str
    window: int = 60
    k: float = 2.0
    state: MeanRevState = field(default_factory=MeanRevState)
    id: str = "meanrev_micro_v1"

    def on_tick(self, l1: Dict[str, float]) -> Optional[Dict[str, object]]:
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            return None
        self.state.prices.append(float(last))
        bb = bollinger(self.state.prices, self.window, self.k)
        if not bb:
            return None
        mid, lower, upper = bb
        if last < lower:
            return {
                "action": "buy",
                "reason": "below_lower_band",
                "features": {"mid": mid, "lower": lower, "upper": upper},
            }
        return None

