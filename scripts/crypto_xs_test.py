"""The crypto cross-sectional test, exactly as registered.

Runs the four configurations fixed in `docs/CRYPTO_REOPENING_PREREGISTRATION.md`
and reports all four, including the three that will not be the best one.

Nothing is tunable. Signals, lookbacks, the horizon, the significance bar and
the date split are constants rather than arguments, because a flag is an
invitation to re-run with a different value and report the one that worked.

Symbols enter the panel when they begin trading rather than being backfilled --
ARB has 1,226 days of history and SEI 1,081 against the panel's 1,430, and
treating either as present earlier is the crypto analogue of survivorship bias.
That is what `membership` carries.

    python scripts/crypto_xs_test.py            # primary window
    python scripts/crypto_xs_test.py --holdout  # only after the primary is recorded
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from intradyne.research.cross_sectional import (  # noqa: E402
    Panel,
    bonferroni_alpha,
    format_results,
    run_test,
    sanity_check,
)

BARS = Path("data/horizon/bars")
#: One rebalance a day. The only horizon with usable power on this panel: a day
#: gives 1,429 windows and a 15.3 bps detectable effect, a week 204 and 158.9.
HOURS_PER_STEP = 24
LOOKBACKS = (5, 21)
HOLD = 1
N_TESTS = 4
N_BOOT = 200
SEED = 20260908
#: Measured on this venue. The taker figure is what execution actually costs
#: today; the maker figure is realistic at a daily rebalance in a way it was
#: not at two minutes, and both are reported because the verdict differs.
COST_TAKER_BPS = 15.0
COST_MAKER_BPS = 9.0
#: 2025-09-01. Everything before is primary, everything after is the hold-out.
SPLIT_S = 1756684800


def build_panel(holdout: bool) -> Panel:
    by = defaultdict(list)
    for f in sorted(BARS.glob("*.npz")):
        by[f.name.split("-")[0]].append(f)

    series = {}
    for s, fs in by.items():
        ts, cl = [], []
        for f in fs:
            d = np.load(f)
            ts.append(d["ts"])
            cl.append(d["close"])
        t = np.concatenate(ts)
        c = np.concatenate(cl)
        o = np.argsort(t)
        series[s] = (t[o], c[o])

    grid = np.array(sorted(set().union(*[set(t.tolist()) for t, _ in series.values()])))
    grid = grid[(grid >= SPLIT_S) if holdout else (grid < SPLIT_S)]
    grid = grid[::HOURS_PER_STEP]

    symbols = sorted(series)
    close = np.full((len(grid), len(symbols)), np.nan)
    for j, s in enumerate(symbols):
        t, c = series[s]
        keep = np.isin(grid, t)
        idx = np.searchsorted(t, grid[keep])
        close[keep, j] = c[idx]

    # A symbol that had not listed yet is not a member. Backfilling it would
    # put prices in the panel that nobody could have traded.
    return Panel(
        dates=grid, symbols=symbols, close=close, membership=np.isfinite(close)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    label = "HOLD-OUT (2025-09 onward)" if args.holdout else "PRIMARY (through 2025-08)"
    print(f"== crypto cross-sectional -- {label} ==")
    alpha = bonferroni_alpha(N_TESTS)
    print(f"resampling null B={N_BOOT}; pass needs p < {alpha:.4f} and edge > cost")
    print(f"costs: taker {COST_TAKER_BPS} bps, maker {COST_MAKER_BPS} bps\n")

    panel = build_panel(args.holdout)
    live = panel.membership.sum(axis=1)
    print(f"panel: {len(panel.dates)} daily steps x {len(panel.symbols)} symbols")
    print(f"       {live.min()} symbols live at the start, {live.max()} at the end\n")

    rows = []
    for weakest in (True, False):
        for lb in LOOKBACKS:
            r = run_test(
                panel,
                lookback=lb,
                hold=HOLD,
                weakest=weakest,
                n_boot=N_BOOT,
                seed=SEED,
                label=f"{'reversal' if weakest else 'momentum'} lb={lb}",
            )
            if r:
                rows.append(r)

    print(format_results(rows, alpha, COST_TAKER_BPS))

    print("\n-- harness checks --")
    problems = sanity_check(rows)
    for p in problems:
        print(f"  PROBLEM: {p}")
    if not problems:
        print("  no faults detected")

    off = [r for r in rows if not (r.null_lo <= 0.0 <= r.null_hi)]
    print(f"  nulls whose 95% band excludes zero: {len(off)}/{len(rows)}")
    if off:
        print("  ABORT: a null that does not bracket zero is not a null.")
        print("  The harness is wrong rather than the market interesting.")
        return 2

    print("\n-- verdict --")
    taker = [r for r in rows if r.p_value < alpha and r.edge_bps > COST_TAKER_BPS]
    maker = [
        r
        for r in rows
        if r.p_value < alpha and COST_MAKER_BPS < r.edge_bps <= COST_TAKER_BPS
    ]
    sig = [r for r in rows if r.p_value < alpha]
    print(f"  significant at p < {alpha:.4f} : {len(sig)}/{len(rows)}")
    print(f"  and above taker cost        : {len(taker)}")
    print(f"  and above maker cost only   : {len(maker)}")
    if not sig:
        print("\n  No configuration beat its resampling null. Under the")
        print("  registration this is a fail, and the override is spent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
