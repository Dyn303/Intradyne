from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from src.core.utils import ALLOWED_UNIVERSE, clamp_weights


Signal = Dict[str, str]
Weights = Dict[str, float]


@dataclass
class BaseStrategy:
    name: str
    universe: List[str]
    max_drawdown: float
    loop_interval_sec: int

    def generate_signals(self, prices: Dict[str, float]) -> Signal:
        raise NotImplementedError

    def allocate_portfolio(self, signals: Signal, portfolio) -> Weights:  # portfolio is src.core.portfolio.Portfolio
        raise NotImplementedError

    def clamp_weights(self, weights: Weights) -> Weights:
        # Ensure allowed symbols only and sum ~ 1.0 with USDT fallback
        w = {k: v for k, v in weights.items() if k in ALLOWED_UNIVERSE}
        w.setdefault("USDT", 0.0)
        return clamp_weights(w)

