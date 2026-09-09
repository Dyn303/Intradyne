"""Validate the cross-sectional harness on free crypto bars.

**This is a harness check, not a research result.** Crypto is closed --
`docs/HORIZON_PREREGISTRATION.md` records the verdict and the bound. Nothing
printed here is evidence about crypto, and a positive number would be a reason
to distrust the harness rather than to reopen the programme.

What it checks is that the machinery `docs/SLOT_1_PREREGISTRATION.md` depends
on behaves correctly on real data before it meets equity prices that cost money
per file:

    * the decile ranking selects the names it claims to
    * the resampling null is centred, which the crypto random-entry control
      was not (it sat four sigma from zero and flipped sign with the signal)
    * date clustering produces plausible counts
    * the sanity checks fire on an empty or degenerate run

Usage:
    python scripts/validate_harness.py
"""

from __future__ import annotations

import os
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

#: Overridable so the harness can be validated from a worktree while the
#: bar cache lives in the main checkout.
BARS = Path(os.environ.get("HORIZON_BARS", "data/horizon/bars"))
#: A crypto round trip, from docs/spread_measurements.json. Used only so the
#: verdict column exercises the cost gate -- not because these trades are
#: contemplated.
COST_BPS = 15.0


def build_panel(hours_per_step: int = 24) -> Panel:
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

    common = None
    for _, (t, _) in series.items():
        common = set(t.tolist()) if common is None else (common & set(t.tolist()))
    dates = np.array(sorted(common))[::hours_per_step]

    symbols = sorted(series)
    close = np.full((len(dates), len(symbols)), np.nan)
    for j, s in enumerate(symbols):
        t, c = series[s]
        close[:, j] = c[np.searchsorted(t, dates)]

    # Every symbol is investable on every date here. On the equity panel this
    # is where the quarterly SPUS membership goes, and it is the difference
    # between a point-in-time universe and a survivorship-biased one.
    membership = np.isfinite(close)
    return Panel(dates=dates, symbols=symbols, close=close, membership=membership)


def main() -> int:
    print("== cross-sectional harness validation ==")
    print("Crypto data, because it is free. NOT a test of crypto: that")
    print("programme is closed. A positive number here impugns the harness.\n")

    panel = build_panel()
    print(f"panel: {len(panel.dates)} daily steps x {len(panel.symbols)} symbols")
    print(f"       {panel.membership.sum():,} symbol-dates investable\n")

    alpha = bonferroni_alpha(8)
    rows = []
    for weakest in (True, False):
        for lb in (5, 21):
            for hold in (5, 21):
                r = run_test(
                    panel,
                    lookback=lb,
                    hold=hold,
                    weakest=weakest,
                    n_boot=100,
                    seed=20260905,
                    label=f"{'reversal' if weakest else 'momentum'} lb={lb} hold={hold}",
                )
                if r:
                    rows.append(r)

    print(format_results(rows, alpha, COST_BPS))
    print(f"\nbar: p < {alpha:.5f} (Bonferroni, 8 tests) and edge > {COST_BPS} bps")

    print("\n-- harness checks --")
    problems = sanity_check(rows)
    for p in problems:
        print(f"  PROBLEM: {p}")
    if not problems:
        print("  no faults detected")

    # The null must be centred. The crypto random-entry control was not, and
    # that is what a broken null looks like from outside.
    off = [r for r in rows if not (r.null_lo <= 0.0 <= r.null_hi)]
    print(f"\n  nulls whose 95% band excludes zero: {len(off)}/{len(rows)}")
    if off:
        print("  ^ a null that does not bracket zero is not a null. Investigate")
        print("    before reading any edge in the table above.")
    else:
        print("  ^ every null brackets zero, which is what a centred null does")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
