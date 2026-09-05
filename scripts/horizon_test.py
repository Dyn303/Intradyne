"""The multi-hour horizon test, as registered and amended.

Runs the eight configurations fixed in `docs/HORIZON_PREREGISTRATION.md` and
reports all eight, including the seven that will not be the best one.

Nothing is tunable. Signals, lookbacks, horizons, the significance bar and the
date split are constants, because a flag is an invitation to re-run with a
different value and report the one that worked.

The control is a resampling null (Amendment 1): hourly returns are shuffled
within each symbol, prices rebuilt, and the identical signal run on the result.
That destroys predictability while preserving the return distribution *and* the
selection mechanism -- which matters because both signals select on a window
extremum, and an extremum is a biased sample of prices whenever prints carry
transient noise. A control that enters at a typical bar cannot separate "this
predicts" from "an extremum is not a typical price"; a shuffled series can,
because if the selection alone is profitable the shuffle shows it too.

    python scripts/horizon_test.py            # primary window
    python scripts/horizon_test.py --holdout  # only after the primary is recorded
"""

from __future__ import annotations

import argparse
import statistics as st
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

BARS = Path("data/horizon/bars")
HORIZONS_H = (4, 8)
LOOKBACKS_H = (12, 48)
SIGNALS = ("breakout", "meanrev")
#: Eight tests, so 0.05/8. Fixed here so it cannot be relaxed once the results
#: are in view, which is the only moment anyone ever wants to relax it.
ALPHA = 0.05 / 8
N_BOOT = 200
#: Round trip per symbol from docs/spread_measurements.json. The edge must clear
#: the instrument's own cost -- averaging lets a cheap symbol pay for a dear one.
COST_BPS = {
    "BTCUSDT": 14.00,
    "ETHUSDT": 14.04,
    "XRPUSDT": 14.69,
    "SOLUSDT": 14.96,
    "AVAXUSDT": 15.34,
    "LTCUSDT": 17.90,
}
#: Archive timestamps are in SECONDS, not milliseconds -- bar spacing is 3600.
#: Assuming ms made the date filter always true and put every trade in day zero,
#: so day-clustering silently skipped all eight configurations and the run
#: printed "0 of 8 passed" as though that were a finding.
PRIMARY_END_S = 1756684800  # 2025-09-01
SEED = 20260905


def _load(sym: str) -> Tuple[np.ndarray, np.ndarray]:
    ts: List[np.ndarray] = []
    cl: List[np.ndarray] = []
    for f in sorted(BARS.glob(f"{sym}-1h-*.npz")):
        d = np.load(f)
        ts.append(d["ts"])
        cl.append(d["close"])
    t = np.concatenate(ts)
    c = np.concatenate(cl)
    o = np.argsort(t)
    return t[o], c[o]


def _edge(close: np.ndarray, signal: str, lb: int, hz: int) -> Tuple[float, int]:
    """Mean forward return in bps over non-overlapping signal entries.

    Windows are spaced by the horizon: overlapping windows share price moves
    and are not independent draws.
    """
    n = len(close)
    rets: List[float] = []
    i = lb
    while i < n - hz:
        w = close[i - lb + 1 : i + 1]
        fired = close[i] >= w.max() if signal == "breakout" else close[i] <= w.min()
        if fired:
            rets.append((close[i + hz] / close[i] - 1.0) * 10_000.0)
            i += hz
        else:
            i += 1
    return (st.mean(rets) if rets else 0.0), len(rets)


def _shuffled(close: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Prices rebuilt from the same returns in a random order."""
    r = np.diff(np.log(close))
    rng.shuffle(r)
    return close[0] * np.exp(np.concatenate([[0.0], np.cumsum(r)]))


def run(holdout: bool) -> int:
    rng = np.random.default_rng(SEED)
    label = "HOLD-OUT (2025-09 onward)" if holdout else "PRIMARY (through 2025-08)"
    print(f"== multi-hour horizon test -- {label} ==")
    print(f"resampling null, B={N_BOOT}; pass needs p < {ALPHA:.5f} and edge > cost\n")

    data: Dict[str, np.ndarray] = {}
    for sym in COST_BPS:
        t, c = _load(sym)
        m = (t >= PRIMARY_END_S) if holdout else (t < PRIMARY_END_S)
        data[sym] = c[m]
    print(f"bars per symbol: {len(next(iter(data.values()))):,}\n")

    print(
        f"{'signal':10} {'lb':>3} {'hz':>3} {'n':>7} {'edge':>8} "
        f"{'null mean':>10} {'null 95%':>18} {'p':>8} {'cost':>6} {'verdict':>8}"
    )
    evaluated = 0
    passed = 0

    for signal in SIGNALS:
        for lb in LOOKBACKS_H:
            for hz in HORIZONS_H:
                real: List[float] = []
                null: List[List[float]] = [[] for _ in range(N_BOOT)]
                costs: List[float] = []
                n_tot = 0
                for sym, close in data.items():
                    e, n = _edge(close, signal, lb, hz)
                    if n == 0:
                        continue
                    real.append(e)
                    costs.append(COST_BPS[sym])
                    n_tot += n
                    for b in range(N_BOOT):
                        ne, nn = _edge(_shuffled(close, rng), signal, lb, hz)
                        if nn:
                            null[b].append(ne)
                if not real:
                    continue

                evaluated += 1
                edge = st.mean(real)
                dist = sorted(st.mean(b) for b in null if b)
                lo = dist[int(0.025 * len(dist))]
                hi = dist[int(0.975 * len(dist)) - 1]
                # Two-sided empirical p: how often the null is at least this
                # extreme. +1 so a p of exactly zero is never claimed from a
                # finite number of draws.
                more = sum(1 for d in dist if abs(d) >= abs(edge))
                p = (more + 1) / (len(dist) + 1)
                cost = st.mean(costs)

                ok = p < ALPHA and edge > cost
                passed += ok
                verdict = "PASS" if ok else ("p only" if p < ALPHA else "no")
                print(
                    f"{signal:10} {lb:3d} {hz:3d} {n_tot:7,} {edge:+8.2f} "
                    f"{st.mean(dist):+10.2f} [{lo:+7.2f},{hi:+7.2f}] "
                    f"{p:8.4f} {cost:6.2f} {verdict:>8}"
                )

    if evaluated == 0:
        print("\n  NOTHING WAS EVALUATED. A harness fault, not a result: an empty")
        print("  table is not the same finding as eight failures, and reporting")
        print("  it as one would close the programme on a bug.")
        return 3

    print(f"\n-- result: {passed} of {evaluated} configurations passed --")
    if passed == 0:
        print("  No configuration beat its resampling null at the registered bar")
        print("  by more than its cost. Under HORIZON_PREREGISTRATION.md: fail.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    return run(ap.parse_args().holdout)


if __name__ == "__main__":
    raise SystemExit(main())
