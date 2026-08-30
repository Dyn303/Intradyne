#!/usr/bin/env python
"""Walk-forward the signal library across months of bars.

    python scripts/strategy_months.py --data DIR --symbol ETHUSDT \
        --timeframe 1m --from 2024-01 --to 2026-07

The four-day tick screen left exactly one door open. Gross edge grew with
holding period (+1.3bps at 2min, +4.8bps at 15min, +14.8bps at 1h), but the
long horizons had only 27-42 non-overlapping trades, so the standard error
swamped the estimate. Months of bars close that door one way or the other.

Two things change with a longer sample, and both matter.

**Drift becomes the thing to beat.** Over a period when the asset rose, any
long-only rule earns the drift, and at hour-plus horizons the drift is large
relative to costs. Beating zero is therefore meaningless here. The comparison
is entering at random -- the unconditional mean over the same bars, which
carries exactly the same drift -- so what gets measured is the signal's
contribution and not the market's.

**One split becomes many.** Ranking on a train set and reporting the winner's
test score still flatters, because the winner was chosen by looking. Walk-
forward asks the question that actually matters: pick the best strategy on
each fold, trade it on the *next* fold, and see what that procedure earns.
That is the number a live deployment would experience.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_klines_archive import FIELDS, TF_SECONDS, months  # noqa: E402
from strategy_search import (  # noqa: E402
    Bars,
    build_strategies,
    evaluate,
    forward_outcomes,
    null_threshold,
)


def load_months(cache: Path, symbol: str, tf: str, start: str, end: str) -> Bars:
    parts = []
    for m in months(start, end):
        npz = cache / f"{symbol}-{tf}-{m}.npz"
        if npz.exists():
            z = np.load(npz)
            parts.append({k: z[k] for k in FIELDS})
    if not parts:
        raise SystemExit(f"no cached months for {symbol} {tf}")
    return Bars(**{k: np.concatenate([p[k] for p in parts]) for k in FIELDS}), len(
        parts
    )


def screen(bars: Bars, gross, held, min_trades: int, strategies):
    rows = []
    for name, fn in strategies.items():
        try:
            m = np.asarray(fn(bars), dtype=bool)
        except Exception:  # noqa: BLE001
            continue
        r = evaluate(m, gross, held, 1)
        if r["trades"] >= min_trades:
            rows.append((name, r))
    rows.sort(key=lambda kv: -kv[1]["mean_bps"])
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--folds", type=int, default=6)
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--cost-bps", type=float, default=14.0)
    p.add_argument("--top", type=int, default=5)
    p.add_argument(
        "--horizons",
        default="15,60,240,480",
        help="Holding periods in bars (1m bars -> minutes)",
    )
    args = p.parse_args(argv)

    bars, n_months = load_months(
        Path(args.data) / "bars", args.symbol, args.timeframe, args.start, args.end
    )
    strategies = build_strategies()
    print(
        f"{args.symbol} {args.timeframe}: {len(bars):,} bars over {n_months} months "
        f"| {len(strategies)} strategies | cost {args.cost_bps:g}bps"
    )
    span_days = (bars.ts[-1] - bars.ts[0]) / 86400.0
    print(
        f"span {span_days:.0f} days, "
        f"price {bars.close[0]:.0f} -> {bars.close[-1]:.0f} "
        f"({(bars.close[-1] / bars.close[0] - 1) * 100:+.0f}%)\n"
    )

    rng = np.random.default_rng(17)
    for hz in [int(x) for x in args.horizons.split(",")]:
        # Payoff geometry scaled to elapsed time, not bar count: a target the
        # move cannot reach in the time allowed makes the time stop the only
        # exit, and "300 bars" means five minutes or five hours depending on
        # the timeframe.
        minutes = hz * TF_SECONDS[args.timeframe] / 60.0
        tp = max(40.0, minutes * 2.5)
        sl = tp * 0.6
        gross, held = forward_outcomes(bars, tp, sl, hz, 0.0)
        drift = float(np.nanmean(gross))

        bounds = np.linspace(0, len(bars), args.folds + 1, dtype=int)
        print(
            f"=== horizon {hz} bars ({minutes:.0f} min) | tp {tp:.0f} / sl {sl:.0f} bps | "
            f"random entry earns {drift:+.2f} bps gross "
            f"({drift - args.cost_bps:+.2f} net) ==="
        )

        picks = []
        for i in range(args.folds - 1):
            tr = slice(bounds[i], bounds[i + 1])
            te = slice(bounds[i + 1], bounds[i + 2])
            btr = Bars(**{k: getattr(bars, k)[tr] for k in FIELDS})
            bte = Bars(**{k: getattr(bars, k)[te] for k in FIELDS})
            gtr, htr = forward_outcomes(btr, tp, sl, hz, 0.0)
            gte, hte = forward_outcomes(bte, tp, sl, hz, 0.0)
            rows = screen(btr, gtr, htr, args.min_trades, strategies)
            if not rows:
                continue
            name, best = rows[0]
            te_r = evaluate(np.asarray(strategies[name](bte), dtype=bool), gte, hte, 1)
            te_drift = float(np.nanmean(gte))
            # Excess over drift is the only part attributable to the signal.
            excess = te_r["mean_bps"] - te_drift
            picks.append((name, te_r["mean_bps"], te_drift, excess, te_r["trades"]))
            print(
                f"  fold {i + 1}->{i + 2}: picked {name:22} "
                f"train {best['mean_bps']:+7.2f} | test {te_r['mean_bps']:+7.2f} "
                f"vs drift {te_drift:+7.2f} = excess {excess:+7.2f} "
                f"({te_r['trades']} trades)"
            )

        if picks:
            ex = np.array([p[3] for p in picks])
            se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else float("nan")
            print(
                f"  walk-forward excess over drift: {ex.mean():+.2f} bps "
                f"+/-{se:.2f}, positive in {int((ex > 0).sum())}/{len(ex)} folds"
            )

        rows = screen(bars, gross, held, args.min_trades, strategies)
        if rows:
            thr = null_threshold(
                gross, held, [r["trades"] for _, r in rows], len(rows), 60, rng, 1
            )
            print(
                f"  full-sample best-of-{len(rows)} under no edge: {thr:+.2f} bps "
                f"(drift {drift:+.2f})"
            )
            for name, r in rows[: args.top]:
                flag = "clears null" if r["mean_bps"] > thr else "within noise"
                print(
                    f"    {name:24} {r['trades']:6d} trades "
                    f"{r['mean_bps']:+7.2f} gross "
                    f"{r['mean_bps'] - args.cost_bps:+7.2f} net  {flag}"
                )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
