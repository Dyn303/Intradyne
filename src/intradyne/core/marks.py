"""Recent price observations, kept in memory.

The flash-crash guardrail compares the current price against the price an hour
ago. It was wired to a stub that returned ``None`` for every symbol, so the
check could never fire. This is the store that makes it real: the engine loop
records every tick here, and API-submitted orders record their mark, so the
guardrail has something to compare against on either path.

In memory rather than on disk deliberately. A restart loses the window, and
the flash-crash check then declines to fire until an hour of observations has
accumulated -- which is the safe direction, since firing on a window it cannot
actually measure would be worse than not firing.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional, Tuple

#: Kept slightly longer than the 1h lookback so the hour-ago sample is still
#: present when it is asked for.
DEFAULT_WINDOW_SECONDS = 3900.0

#: How far from the requested instant an observation may be and still answer
#: for it. Without this, a store holding only five minutes of data would
#: answer a "price one hour ago" query with a five-minute-old price, and a
#: five-minute move would be reported as an hourly crash.
DEFAULT_TOLERANCE_SECONDS = 300.0


def _to_epoch(at: Optional[datetime]) -> Optional[float]:
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.timestamp()


class MarkStore:
    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    ) -> None:
        self.window_seconds = float(window_seconds)
        self.tolerance_seconds = float(tolerance_seconds)
        self._lock = threading.Lock()
        self._series: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)

    def record(self, symbol: str, price: float, ts: Optional[float] = None) -> None:
        if price is None or price <= 0:
            return
        now = float(ts if ts is not None else time.time())
        with self._lock:
            series = self._series[symbol]
            series.append((now, float(price)))
            cutoff = now - self.window_seconds
            while series and series[0][0] < cutoff:
                series.popleft()

    def latest(self, symbol: str) -> Optional[float]:
        with self._lock:
            series = self._series.get(symbol)
            return series[-1][1] if series else None

    def marks(self) -> Dict[str, float]:
        """Last price per symbol, for valuing a portfolio."""
        with self._lock:
            return {s: v[-1][1] for s, v in self._series.items() if v}

    def get(self, symbol: str, at: Optional[datetime] = None) -> Optional[float]:
        """Price at `at`, or the latest when `at` is None.

        Returns None when no observation lies within the tolerance of `at`,
        so a caller asking about an hour ago cannot be silently handed a much
        more recent price.
        """
        target = _to_epoch(at)
        with self._lock:
            series = self._series.get(symbol)
            if not series:
                return None
            if target is None:
                return series[-1][1]
            best: Optional[Tuple[float, float]] = None
            for ts, price in series:
                delta = abs(ts - target)
                if best is None or delta < best[0]:
                    best = (delta, price)
            if best is None or best[0] > self.tolerance_seconds:
                return None
            return best[1]

    def clear(self) -> None:
        with self._lock:
            self._series.clear()


__all__ = ["MarkStore", "DEFAULT_WINDOW_SECONDS", "DEFAULT_TOLERANCE_SECONDS"]
