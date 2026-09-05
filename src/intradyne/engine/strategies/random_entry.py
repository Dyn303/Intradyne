"""A control strategy that enters at random times.

Stage 2 asks whether the entry rule's moments are different from other
moments. Answering that needs something to compare against, and "nothing" is
not a comparison: a strategy can look bad in absolute terms and still be
better than the alternative, or look fine and be worse. The control is the
alternative that costs nothing to implement -- entering at arbitrary times and
holding under the same rules.

It ignores price entirely. That is the point: any edge it shows comes from the
holding rules and the market's own drift, which is exactly the component the
real strategy must beat rather than merely reproduce.

See `docs/STAGE_2_PREREGISTRATION.md`. The control there was first written as
random *side* at matched timestamps; that is unimplementable here, because
`forbid_shorting` refuses shorts at the compliance layer, and it tests the
wrong thing for a long-only rule. Amended to random *time* before the run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass
class RandomEntryStrategy:
    """Emits a buy on a fixed per-tick probability, ignoring the market.

    `p` is the chance of signalling on any one tick for one symbol. At a 1s
    feed interval, p = 0.004 is roughly one signal per symbol every four
    minutes; the realised rate is lower, because position capacity and the
    concurrent-position limit refuse many of them -- exactly as they do for
    the real strategy, which is why the comparison stays fair.

    Seeded so a run can be repeated. The seed is per-symbol, so two symbols do
    not fire in lockstep and produce spuriously correlated trades.
    """

    symbol: str
    p: float = 0.004
    seed: int = 0
    id: str = "random_entry_control"
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(f"{self.seed}:{self.symbol}")

    def on_tick(self, l1: Mapping[str, Any]) -> Optional[Dict[str, object]]:
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            # No price is not an opportunity to trade blind. The real
            # strategies return here too, so the control sees the same ticks.
            return None
        if self._rng.random() >= self.p:
            return None
        return {
            "action": "buy",
            "reason": "random_control",
            "features": {"p": self.p},
        }
