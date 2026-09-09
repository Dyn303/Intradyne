from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Mapping, Optional


@dataclass
class MomentumState:
    #: Ticks, not seconds. 120 of them is two minutes only while the feed
    #: delivers at `data_ws.TARGET_INTERVAL_S`; see MomentumStrategy below.
    prices: Deque[float] = field(default_factory=lambda: deque(maxlen=120))


@dataclass
class MomentumStrategy:
    """A breakout scalper whose lookback is counted in ticks.

    `breakout_window` is a **number of ticks**, though it was commented as
    seconds; the two coincide only when the feed delivers one tick per second.
    `time_stop_s` really is seconds -- `router.py:231` compares wall clock --
    and that asymmetry is the trap: if the feed slows, the window stretches
    while the stop does not, so a position can time out before its lookback
    has even filled. `DataFeed.interval_s` reports the live conversion factor
    and the feed warns when it drifts from the target.
    """

    symbol: str
    #: Ticks. 60 at a 1s interval is a minute; at 10s it is ten.
    breakout_window: int = 60
    min_range_bps: int = 5
    time_stop_s: int = 120
    retest_pct: float = 0.0  # allow entry if within pct below breakout high
    state: MomentumState = field(default_factory=MomentumState)
    id: str = "mom_scalper_v1"

    def on_tick(self, l1: Mapping[str, Any]) -> Optional[Dict[str, object]]:
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            return None
        self.state.prices.append(float(last))
        if len(self.state.prices) < self.breakout_window:
            return None
        window = list(self.state.prices)[-self.breakout_window :]
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
        # optional retest entry: permit slight pullback from highs
        if (
            self.retest_pct > 0
            and range_bps >= self.min_range_bps
            and last >= pmax * (1.0 - float(self.retest_pct))
        ):
            return {
                "action": "buy",
                "reason": "retest",
                "features": {"range_bps": range_bps, "pmax": pmax},
            }
        return None
