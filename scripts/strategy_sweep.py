#!/usr/bin/env python
"""Ask whether *any* configuration lets a signal clear its costs.

    python scripts/strategy_sweep.py --data DIR

The 50-signal screen found every strategy losing 11-13bps against a 14bps
round-trip cost -- gross edges of roughly 1-2bps. That is a statement about
costs, not about signals, so the useful follow-up is not "which signal?" but
"is there a holding period and payoff geometry where a ~1-2bps gross edge can
survive at all?"

This loads the bars once and re-runs the whole signal library across horizons,
targets and fee assumptions. For each configuration it reports the best
strategy, the gross edge it earned before costs, and whether anything clears
both the selection-bias null and a held-out sample.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_search import (  # noqa: E402
    Bars,
    build_strategies,
    evaluate,
    forward_outcomes,
    load_bars,
    null_threshold,
)

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


def cached_bars(root: Path, symbol: str, days: str, cache: Path) -> Bars:
    parts = []
    for d in [x.strip() for x in days.split(",") if x.strip()]:
        npz = cache / f"{symbol}-{d}.npz"
        if npz.exists():
            z = np.load(npz)
            parts.append(Bars(**{k: z[k] for k in FIELDS}))
        else:
            b = load_bars(root / f"{symbol}-aggTrades-{d}.csv", 1)
            cache.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(npz, **{k: getattr(b, k) for k in FIELDS})
            parts.append(b)
        print(f"  {d}: {len(parts[-1]):,} bars", flush=True)
    return Bars(**{k: np.concatenate([getattr(b, k) for b in parts]) for k in FIELDS})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--train", default="2026-08-26,2026-08-27")
    p.add_argument("--test", default="2026-08-28,2026-08-29")
    p.add_argument("--min-trades", type=int, default=100)
    args = p.parse_args(argv)

    root, cache = Path(args.data), Path(args.data) / "bars"
    print("training days")
    train = cached_bars(root, args.symbol, args.train, cache)
    print("held-out days")
    test = cached_bars(root, args.symbol, args.test, cache)
    strategies = build_strategies()

    # (label, round-trip cost in bps)
    #   taker both legs      = 2 x (5 fee + 2 slippage)          = 14
    #   maker entry, taker exit = 2 + (5 + 2)                    =  9
    #   maker both legs      = 2 x 2                             =  4
    #   zero                 = the gross edge, for reference     =  0
    COSTS = [
        ("taker 14bps", 14.0),
        ("maker entry 9bps", 9.0),
        ("maker both 4bps", 4.0),
        ("no cost", 0.0),
    ]
    # (take-profit, stop-loss, horizon in seconds)
    GEOM = [
        (20, 30, 120),
        (40, 20, 300),
        (60, 40, 900),
        (100, 60, 1800),
        (200, 120, 3600),
        (400, 250, 7200),
    ]

    rng = np.random.default_rng(11)
    hdr = (
        f"{'geometry':22} {'cost':18} {'best strategy':22} {'gross':>7} "
        f"{'net':>8} {'null':>8} {'test':>8}  verdict"
    )
    print("\n" + hdr)
    print("-" * len(hdr))

    survivors = []
    for tp, sl, hz in GEOM:
        gross_tr, held_tr = forward_outcomes(train, tp, sl, hz, 0.0)
        gross_te, held_te = forward_outcomes(test, tp, sl, hz, 0.0)
        # Rank once per geometry on the gross series; costs are a constant
        # shift per trade, so they cannot reorder the ranking.
        rows = []
        for name, fn in strategies.items():
            try:
                m = np.asarray(fn(train), dtype=bool)
            except Exception:
                continue
            r = evaluate(m, gross_tr, held_tr, 1)
            if r["trades"] >= args.min_trades:
                rows.append((name, r))
        if not rows:
            continue
        rows.sort(key=lambda kv: -kv[1]["mean_bps"])
        name, best = rows[0]
        te = evaluate(
            np.asarray(strategies[name](test), dtype=bool), gross_te, held_te, 1
        )
        thr_gross = null_threshold(
            gross_tr, held_tr, [r["trades"] for _, r in rows], len(rows), 60, rng, 1
        )
        geom = f"tp{tp}/sl{sl}/{hz}s"
        for label, cost in COSTS:
            net, null_net, test_net = (
                best["mean_bps"] - cost,
                thr_gross - cost,
                te["mean_bps"] - cost,
            )
            if net <= null_net:
                verdict = "within noise"
            elif test_net <= 0:
                verdict = "fails out of sample"
            else:
                verdict = "SURVIVES"
                survivors.append((geom, label, name, net, test_net))
            print(
                f"{geom:22} {label:18} {name:22} {best['mean_bps']:+7.2f} "
                f"{net:+8.2f} {null_net:+8.2f} {test_net:+8.2f}  {verdict}"
            )
        print()

    if survivors:
        print("configurations where a signal clears both bars:")
        for geom, label, name, net, te_net in survivors:
            print(
                f"  {name} @ {geom}, {label}: train {net:+.2f} test {te_net:+.2f} bps"
            )
    else:
        print("No signal clears the null and holds out at any geometry or fee level.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
