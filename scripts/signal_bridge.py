#!/usr/bin/env python
"""Run a research signal inside the engine, and prove the two agree.

    python scripts/signal_bridge.py --report

Research signals are `SignalFn = Callable[[Bars], np.ndarray]` -- vectorised
over the whole history, returning a boolean mask. Engine strategies implement
`on_tick` and see one update at a time. They share no code, so a rule validated
in research has to be re-implemented by hand before it can trade, and nothing
checks the re-implementation. That is how a measured edge quietly becomes a
different strategy.

`VectorSignal` removes the re-implementation: it holds a rolling buffer of bars
and calls the research function itself, so the engine path *is* the research
path. Drift is then impossible by construction rather than by discipline.

What is not free is the truncation. A vectorised function evaluated over a
buffer of the last B bars is not obliged to agree with the same function
evaluated over the full history at that index, and two different reasons for
disagreement matter in opposite ways.

**Insufficient buffer** is benign and fixable. `_roll_mean` and friends look
back a fixed `n`, so any buffer of at least `n` reproduces them exactly.
`_ema` seeds from `out[0] = x[0]` and decays without ever formally forgetting,
so in principle it is only approximable -- but in practice the seed's influence
falls below floating-point resolution, and measurement shows every EMA signal
here reproducing exactly at a longer buffer than the finite-window ones need.
That is the sort of claim worth measuring rather than reasoning about, which is
what `minimum_buffer()` is for.

**Look-ahead is a defect**, and this harness detects it for free. A streaming
run has, by construction, only the bars up to the current index. If a signal's
offline value at `i` disagrees with its streamed value at `i` no matter how
large the buffer, the offline computation is using data from after `i`. That is
amendment-level: the framework rejects look-ahead as a rejection condition, and
until now nothing in this repo could test for it mechanically.

So the bridge is two things, and the second is the more valuable: an adapter
that lets one definition serve both runtimes, and a test that a signal is
causal.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy_search import Bars, SignalFn, build_strategies  # noqa: E402

#: Fields a Bars carries, in the order the dataclass declares them.
FIELDS = (
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "buy_volume",
    "sell_volume",
    "trades",
)


def slice_bars(b: Bars, lo: int, hi: int) -> Bars:
    """The bars in `[lo, hi)`, as a Bars."""
    return Bars(**{f: getattr(b, f)[lo:hi] for f in FIELDS})


@dataclass
class VectorSignal:
    """A research `SignalFn`, driven one bar at a time.

    The engine calls `on_bar`; the research function sees a `Bars` built from
    the rolling buffer. `buffer_bars` must be at least the signal's longest
    lookback -- `minimum_buffer()` measures it rather than requiring a guess.

    Bars rather than ticks is deliberate. Research signals are defined over
    bars of a stated width, and the engine's existing strategies quietly assume
    one tick per second (`deque(maxlen=120)` commented "2m at 1s"), which is an
    assumption about arrival rate rather than about time. Aggregating ticks
    into bars is a separate job with its own correctness question, and folding
    it in here would hide it.
    """

    fn: SignalFn
    buffer_bars: int
    symbol: str = ""
    id: str = "vector_signal"
    reason: str = "research_signal"
    _buf: Dict[str, Deque[float]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for f in FIELDS:
            self._buf[f] = deque(maxlen=self.buffer_bars)

    def __len__(self) -> int:
        return len(self._buf["close"])

    def on_bar(self, bar: Mapping[str, Any]) -> Optional[Dict[str, object]]:
        """Append a bar and report whether the signal fires on it.

        Returns the router's shape -- `{"action": "buy", ...}` or None -- so a
        `VectorSignal` satisfies the same contract as `MomentumStrategy`.
        """
        for f in FIELDS:
            self._buf[f].append(float(bar.get(f, 0.0) or 0.0))
        if len(self) < 2:
            return None
        window = Bars(**{f: np.asarray(self._buf[f], dtype=float) for f in FIELDS})
        try:
            mask = np.asarray(self.fn(window))
        except (ValueError, IndexError, FloatingPointError):
            # A window too short for this signal's lookback is not an error;
            # it is the warm-up, and it means "no signal yet".
            return None
        if mask.size == 0 or not bool(mask[-1]):
            return None
        return {
            "action": "buy",
            "reason": self.reason,
            "features": {"bars": len(self)},
        }


def replay(fn: SignalFn, bars: Bars, buffer_bars: int) -> np.ndarray:
    """The mask a streaming run produces, one bar at a time.

    Each element is decided using only the bars up to and including it, which
    is what makes a disagreement with the offline mask meaningful.
    """
    sig = VectorSignal(fn=fn, buffer_bars=buffer_bars)
    out = np.zeros(len(bars), dtype=bool)
    for i in range(len(bars)):
        bar = {f: float(getattr(bars, f)[i]) for f in FIELDS}
        out[i] = sig.on_bar(bar) is not None
    return out


def _offline(fn: SignalFn, bars: Bars) -> np.ndarray:
    m = np.asarray(fn(bars))
    return np.where(np.isnan(m.astype(float)) if m.dtype != bool else False, False, m)


def agreement(
    fn: SignalFn, bars: Bars, buffer_bars: int, warmup: Optional[int] = None
) -> Dict[str, Any]:
    """Compare a streamed run against the offline mask.

    `warmup` bars at the start are excluded: a streaming run genuinely cannot
    know them, and counting them as disagreements would flag every finite
    lookback as broken. It defaults to the buffer length, which is the most
    conservative honest choice.
    """
    off = _offline(fn, bars).astype(bool)
    on = replay(fn, bars, buffer_bars)
    w = buffer_bars if warmup is None else warmup
    off_t, on_t = off[w:], on[w:]
    diff = int(np.sum(off_t != on_t))
    return {
        "buffer": buffer_bars,
        "compared": int(off_t.size),
        "offline_fires": int(off_t.sum()),
        "streamed_fires": int(on_t.sum()),
        "disagreements": diff,
        "exact": diff == 0,
    }


def minimum_buffer(
    fn: SignalFn, bars: Bars, candidates: Sequence[int]
) -> Optional[int]:
    """Smallest candidate buffer that reproduces the offline mask exactly.

    Every candidate is judged over the *same* bars. Letting `warmup` follow
    the buffer would hand a long buffer an easier test -- it would be compared
    on fewer bars and could score exact by being examined less, which is the
    harness flattering itself rather than the signal earning it.
    """
    w = max(candidates)
    for b in sorted(candidates):
        if agreement(fn, bars, b, warmup=w)["exact"]:
            return b
    return None


def synthetic_bars(n: int = 900, seed: int = 7) -> Bars:
    """Deterministic bars. Equivalence is a property of the code, not the data.

    A geometric random walk with a volume series -- enough structure for the
    rolling statistics to be non-degenerate, and reproducible so a failure is
    a real disagreement rather than a different draw.
    """
    rng = np.random.default_rng(seed)
    ret = rng.standard_normal(n) * 0.0008
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.standard_normal(n)) * 0.0004)
    low = close * (1.0 - np.abs(rng.standard_normal(n)) * 0.0004)
    vol = rng.integers(500, 5000, n).astype(float)
    buy = vol * rng.uniform(0.3, 0.7, n)
    return Bars(
        ts=np.arange(n, dtype=float),
        open=np.concatenate([[close[0]], close[:-1]]),
        high=high,
        low=low,
        close=close,
        volume=vol,
        buy_volume=buy,
        sell_volume=vol - buy,
        trades=rng.integers(5, 80, n).astype(float),
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--bars", type=int, default=900)
    ap.add_argument("--limit", type=int, default=24)
    args = ap.parse_args(argv)

    bars = synthetic_bars(args.bars)
    strategies = build_strategies()
    names = sorted(strategies)[: args.limit]
    cands = (60, 120, 240, 400)

    print(f"signal bridge: {len(names)} signals, {len(bars)} bars")
    print(f"buffers tried: {cands}")
    print()
    print(f"{'signal':<26}{'min buffer':>12}{'verdict':>34}")
    print("-" * 72)
    exact = approx = 0
    for name in names:
        fn = strategies[name]
        mb = minimum_buffer(fn, bars, cands)
        if mb is not None:
            exact += 1
            print(f"{name:<26}{mb:>12}{'exact -- safe to trade':>34}")
        else:
            approx += 1
            a = agreement(fn, bars, max(cands))
            pct = 100.0 * a["disagreements"] / max(a["compared"], 1)
            print(f"{name:<26}{'-':>12}{f'approx only, {pct:.2f}% differ':>34}")
    print()
    print(f"exactly reproducible: {exact}/{len(names)}")
    print(f"approximate only    : {approx}/{len(names)}")
    print()
    print("An 'approx only' signal is not broken -- unbounded-memory terms such")
    print("as an EMA cannot be reproduced from a truncated buffer. It means the")
    print("streamed entries will differ slightly from the researched ones, and")
    print("that difference belongs in the decision memo rather than in a")
    print("surprise. A signal that stays inexact at every buffer length and")
    print("differs a great deal is a different matter: check it for look-ahead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
