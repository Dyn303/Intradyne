from __future__ import annotations

from typing import Dict, List

from ..core.utils import ALLOWED_UNIVERSE


class BaseStrategy:
    def __init__(self) -> None:
        self.universe: List[str] = ALLOWED_UNIVERSE

    def generate_signals(self, prices: Dict[str, float]) -> Dict[str, float]:
        # Simple momentum proxy: signal = price / mean(price in universe)
        if not prices:
            return {s: 0.0 for s in self.universe}
        avg = sum(prices.get(s, 0.0) for s in self.universe if s != "USDT") / max(
            1, len(self.universe) - 1
        )
        signals = {}
        for s in self.universe:
            if s == "USDT":
                signals[s] = 0.0
            else:
                p = prices.get(s, avg or 1.0)
                signals[s] = p / (avg or 1.0)
        return signals

    def allocate_portfolio(
        self, signals: Dict[str, float], portfolio
    ) -> Dict[str, float]:
        # Convert signals to positive weights and normalize; include some cash (USDT)
        pos = {
            k: max(0.0, v)
            for k, v in signals.items()
            if k in self.universe and k != "USDT"
        }
        total = sum(pos.values())
        weights: Dict[str, float] = {}
        if total <= 0:
            # All to cash
            weights["USDT"] = 1.0
        else:
            cash = 0.1
            scale = (1.0 - cash) / total
            for k, v in pos.items():
                weights[k] = v * scale
            weights["USDT"] = cash
        # Ensure sum to 1.0
        s = sum(weights.values())
        if s != 1.0 and s > 0:
            # Adjust cash to close rounding
            weights["USDT"] = max(0.0, weights.get("USDT", 0.0) + (1.0 - s))
        return weights
