#!/usr/bin/env python
"""Run the intraday strategy search pooled across many instruments.

    python scripts/multi_instrument_search.py --data DIR --symbols BTCUSDT,ETHUSDT,...

The single-instrument version reached a clear diagnosis: at genuine intraday
horizons some strategies clear their costs, but none beats the best-of-N null
for its own trade count. On ETH the leader scored +12.00bps against a null of
+28.86 -- because 206 trades at tp300/sl150 is a small, high-variance sample,
and picking the best of 55 candidates from that produces +28bps by luck.

The fix is more *trades*, not more strategies. Applying one strategy across
twenty instruments multiplies its sample without multiplying the number of
things being selected over, and the null falls as 1/sqrt(n) while the measured
edge does not. Adding strategies does the opposite: it raises the null and
buys nothing.

This is also what the literature prescribes. Multiple-testing work on
published anomalies puts the credible hurdle at t ~ 3.4-3.8 rather than 1.96,
with roughly 45% of classic-threshold "discoveries" false; White's Reality
Check and Hansen's SPA test formalise what the best-of-N null approximates
here by simulation.

Pooling is per-trade, not per-instrument-average. A strategy that fires 20
times on one coin and twice on another is mostly a statement about the first,
and averaging instrument means would hide that. Per-instrument breakdown is
reported so a result driven by one name is visible rather than buried.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multi_common import (  # noqa: E402
    COST_MAKER_BPS,
    COST_TAKER_BPS,
    MIN_TRADES,
    Strategy,
    load_symbol,
    sample_strategies,
    strategy_trades,
)


def pooled(
    s: Strategy,
    panels: Dict[str, Any],
    bar_minutes: int,
) -> Dict[str, Any]:
    """Every trade the strategy takes, across every instrument."""
    all_r: List[np.ndarray] = []
    all_t: List[np.ndarray] = []
    per_sym: Dict[str, Tuple[int, float]] = {}
    holds: List[float] = []
    for sym, p in panels.items():
        r, ts, hold = strategy_trades(s, p, bar_minutes)
        if r.size:
            all_r.append(r)
            all_t.append(ts)
            per_sym[sym] = (int(r.size), float(r.mean()))
            holds.append(hold)
    if not all_r:
        return {"trades": 0}
    pool = np.concatenate(all_r)
    pool_ts = np.concatenate(all_t)

    # Cluster by day before computing significance.
    #
    # These instruments have a mean pairwise correlation of 0.56 at hourly
    # horizons, which puts the effective number of independent names at
    # roughly 1.7 out of 20. Treating every pooled trade as an independent
    # observation therefore overstates t by about 3x -- a strategy measured
    # at t = 3.91 across 25,946 correlated trades is nearer t = 1.2 once the
    # correlation is respected. Averaging within a day and testing the daily
    # series is the cheap, standard correction.
    day = (pool_ts // 86400).astype("int64")
    order = np.argsort(day)
    day, vals = day[order], pool[order]
    edges = np.flatnonzero(np.diff(day)) + 1
    daily = np.array([g.mean() for g in np.split(vals, edges)])
    se_c = float(daily.std(ddof=1) / np.sqrt(daily.size)) if daily.size > 1 else 0.0
    return {
        "trades": int(pool.size),
        "gross_bps": float(pool.mean()),
        "sd_bps": float(pool.std(ddof=1)) if pool.size > 1 else 0.0,
        "days": int(daily.size),
        "t_clustered": float(daily.mean() / se_c) if se_c > 0 else 0.0,
        "win_rate": float((pool > 0).mean()),
        "median_hold_min": float(np.median(holds)) if holds else 0.0,
        "per_symbol": per_sym,
        "symbols_traded": len(per_sym),
    }


def pooled_null(
    s: Strategy,
    panels: Dict[str, Any],
    bar_minutes: int,
    n_trades: int,
    n_strategies: int,
    draws: int,
    rng: np.random.Generator,
) -> float:
    """Best-of-N pooled edge reachable by entering at random.

    Draws from the same instruments and the same geometry, so the only thing
    that differs from the real strategy is *when* it enters.
    """
    from multi_common import outcomes_for

    pools = []
    for sym, p in panels.items():
        g, _ = outcomes_for(s, p, bar_minutes)
        ok = g[np.isfinite(g)]
        if ok.size:
            pools.append(ok)
    if not pools:
        return float("nan")
    universe = np.concatenate(pools)
    bests = []
    for _ in range(draws):
        best = -np.inf
        for _ in range(n_strategies):
            pick = universe[rng.integers(0, universe.size, size=n_trades)]
            best = max(best, float(pick.mean()))
        bests.append(best)
    return float(np.percentile(bests, 95))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--symbols", required=True)
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--bar-minutes", type=int, default=5)
    p.add_argument("--train", default="2024-01")
    p.add_argument("--train-end", default="2025-08")
    p.add_argument("--test", default="2025-09")
    p.add_argument("--test-end", default="2026-07")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--seed", type=int, default=20260901)
    p.add_argument("--null-draws", type=int, default=30)
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args(argv)

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    cache = Path(args.data) / "bars"
    tr_panels, te_panels = {}, {}
    for sym in syms:
        a = load_symbol(cache, sym, args.timeframe, args.train, args.train_end)
        b = load_symbol(cache, sym, args.timeframe, args.test, args.test_end)
        if a and b:
            tr_panels[sym], te_panels[sym] = a, b
    if not tr_panels:
        print("no data loaded")
        return 1
    bars_tr = sum(len(p["bars"]) for p in tr_panels.values())
    print(f"{len(tr_panels)} instruments, {bars_tr:,} training bars ({args.timeframe})")

    rng = np.random.default_rng(args.seed)
    any_p = next(iter(tr_panels.values()))
    strategies = sample_strategies(
        rng, sorted(any_p["preds"]), list(any_p["regs"]), args.n
    )
    print(f"{len(strategies)} random strategies\n")
    print("filter fixed before running (same tiers as the single-instrument run):")
    print(f"  Tier 0  >= {MIN_TRADES} pooled trades")
    print(f"  Tier 1  gross edge > {COST_MAKER_BPS:g}bps round trip")
    print("  Tier 2  beats its own best-of-N null")
    print("  Tier 3  still positive out of sample")
    print(f"  Tier 4  still positive at {COST_TAKER_BPS:g}bps taker cost\n")

    rows = []
    for s in strategies:
        r = pooled(s, tr_panels, args.bar_minutes)
        if r["trades"] > 0:
            rows.append((s, r))
    print(f"evaluated {len(rows)}/{len(strategies)}")

    t0 = [(s, r) for s, r in rows if r["trades"] >= MIN_TRADES]
    print(f"\nTier 0  >= {MIN_TRADES} pooled trades : {len(t0)}/{len(rows)} pass")
    if t0:
        tr_counts = sorted(r["trades"] for _, r in t0)
        holds = sorted(r["median_hold_min"] for _, r in t0)
        print(
            f"        pooled trades           : median {tr_counts[len(tr_counts) // 2]:,} "
            f"(max {tr_counts[-1]:,})"
        )
        print(f"        actual median hold      : {holds[len(holds) // 2]:.0f} min")

    t1 = [(s, r) for s, r in t0 if r["gross_bps"] > COST_MAKER_BPS]
    print(f"Tier 1  gross > {COST_MAKER_BPS:g}bps        : {len(t1)}/{len(t0)} pass")

    t2 = []
    for s, r in t1:
        thr = pooled_null(
            s, tr_panels, args.bar_minutes, r["trades"], len(rows), args.null_draws, rng
        )
        t_stat = r["t_clustered"]
        ok = r["gross_bps"] > thr
        if ok:
            t2.append((s, r))
        print(
            f"        {'PASS' if ok else '    '} {s.name[:40]:40} "
            f"{r['gross_bps']:+7.2f} vs null {thr:+7.2f}  t={t_stat:5.2f} "
            f"n={r['trades']:,}"
        )
    print(f"Tier 2  beats own null          : {len(t2)}/{len(t1)} pass")

    t3 = []
    for s, r in t2:
        rt = pooled(s, te_panels, args.bar_minutes)
        if rt["trades"] >= 50 and rt["gross_bps"] - COST_MAKER_BPS > 0:
            t3.append((s, r, rt))
    print(f"Tier 3  holds out of sample     : {len(t3)}/{len(t2)} pass")

    t4 = [(s, r, rt) for s, r, rt in t3 if rt["gross_bps"] - COST_TAKER_BPS > 0]
    print(f"Tier 4  survives taker cost     : {len(t4)}/{len(t3)} pass\n")

    # Rank only what cleared Tier 0. Ranking over everything puts a
    # one-trade, 100%-win artifact at the top of the table, which is
    # the exact impression this filter exists to prevent.
    rows = sorted(t0, key=lambda kv: -kv[1]["gross_bps"])
    hdr = (
        f"{'#':>2} {'strategy':44} {'pooled n':>9} {'syms':>5} {'win':>7} "
        f"{'gross':>8} {'t_cl':>6} {'net@4':>8}"
    )
    print(
        f"top {args.top} by pooled gross edge (among the {len(t0)} with >= {MIN_TRADES} trades):"
    )
    print(hdr)
    print("-" * len(hdr))
    for i, (s, r) in enumerate(rows[: args.top], 1):
        se = r["sd_bps"] / np.sqrt(max(1, r["trades"]))
        t_stat = r["gross_bps"] / se if se > 0 else 0.0
        print(
            f"{i:>2} {s.name[:44]:44} {r['trades']:>9,} "
            f"{r['symbols_traded']:>5} {r['win_rate']:7.1%} "
            f"{r['gross_bps']:+8.2f} {t_stat:6.2f} "
            f"{r['gross_bps'] - COST_MAKER_BPS:+8.2f}"
        )

    if rows:
        best_s, best_r = rows[0]
        by = sorted(best_r["per_symbol"].items(), key=lambda kv: -kv[1][0])
        print(f"\nper-instrument breakdown of the leader ({best_s.name[:40]}):")
        print(f"  {'symbol':10} {'trades':>8} {'gross':>8}")
        for sym, (n, m) in by[:8]:
            print(f"  {sym:10} {n:>8,} {m:>+8.2f}")
        pos = sum(1 for _, (_, m) in by if m > 0)
        print(f"  positive on {pos}/{len(by)} instruments")

    print()
    if t4:
        print(f"{len(t4)} strategies cleared every tier:")
        for s, _, _ in t4:
            print(f"  {s.name}")
    else:
        print("No strategy cleared every tier.")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/multi_instrument_search.json").write_text(
        json.dumps(
            {
                "instruments": len(tr_panels),
                "n": len(strategies),
                "tier0": len(t0),
                "tier1": len(t1),
                "tier2": len(t2),
                "tier3": len(t3),
                "tier4": len(t4),
                "top": [
                    {
                        "name": s.name,
                        "trades": r["trades"],
                        "gross_bps": r["gross_bps"],
                        "win_rate": r["win_rate"],
                        "symbols": r["symbols_traded"],
                    }
                    for s, r in rows[:20]
                ],
            },
            indent=1,
        )
    )
    return 0 if t4 else 2


if __name__ == "__main__":
    raise SystemExit(main())
