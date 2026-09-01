#!/usr/bin/env python
"""Generate random intraday strategies and put them through a tiered filter.

    python scripts/random_strategy_search.py --data DIR --symbol ETHUSDT

Everything searched so far tested *single* entry signals. This samples whole
strategies -- entry predicate, confluence requirement, regime filter, exit
geometry and holding period -- which is a materially larger space. Confluence
is the reason it is worth running at all: requiring two or three conditions to
agree trades far less often but more selectively, and trading less often is the
only mechanism that can lift a per-trade edge toward the cost line.

**The tiers are fixed here, before any result exists.** With 100 candidates the
best one clears any fixed bar by luck alone, so the ordering matters:

    Tier 0  at least MIN_TRADES non-overlapping trades
    Tier 1  gross edge per trade exceeds the round-trip cost
    Tier 2  net edge beats the best-of-100 null threshold
    Tier 3  net edge still positive on held-out data
    Tier 4  still positive at taker cost, not just all-maker

Tier 1 is deliberately first and deliberately brutal. The intraday edge on
this instrument has already been measured at roughly 0.5bps per trade at a
two-minute horizon, real at 4-6 sigma, against a round trip of 4bps all-maker
and 14bps taker. A strategy whose gross edge does not clear its own costs
cannot be rescued by anything downstream, so there is no point ranking on
win rate or Sharpe before that question is settled.

Alignment with the live rule system is structural rather than checked after
the fact: every generated strategy is long-only and spot (entry predicates
produce buy signals only, exits close to flat), holds one position per symbol
at a time, and uses a fixed take-profit / stop-loss pair -- the same shape
`RiskManager` enforces. Nothing here can express a short, leverage, or a
position that survives its stop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_months import load_months  # noqa: E402
from strategy_search import (  # noqa: E402
    Bars,
    _ema,
    _roll_max,
    _roll_mean,
    _roll_min,
    _roll_std,
    evaluate,
    forward_outcomes,
)

# ---- the filter, fixed before running -----------------------------------

MIN_TRADES = 200  # Tier 0: below this, nothing is measurable
COST_MAKER_BPS = 4.0  # Tier 1: the most favourable round trip available
COST_TAKER_BPS = 14.0  # Tier 4: what it costs if you cross the spread
REQUIRED_MARGIN_BPS = 0.0  # Tier 1 asks only that gross clears cost


# ---- predicate library --------------------------------------------------


def _predicates(b: Bars) -> Dict[str, np.ndarray]:
    """Boolean conditions a strategy can require. All long-side."""
    n = len(b)
    ret1 = np.diff(b.close, prepend=b.close[0]) / b.close
    out: Dict[str, np.ndarray] = {}

    for w in (5, 15, 30, 60, 120):
        r = np.full(n, np.nan)
        r[w:] = (b.close[w:] / b.close[:-w] - 1.0) * 1e4
        sd = _roll_std(ret1 * 1e4, 60) * np.sqrt(w)
        out[f"mom{w}"] = r > sd
        out[f"fade{w}"] = r < -sd

    for w in (30, 60, 120, 300):
        out[f"break{w}"] = b.close >= _roll_max(b.high, w)
        out[f"low{w}"] = b.close <= _roll_min(b.low, w)

    for w in (30, 60, 120):
        m, sd = _roll_mean(b.close, w), _roll_std(b.close, w)
        out[f"below{w}"] = b.close < m - 1.5 * sd
        out[f"above{w}"] = b.close > m + 1.5 * sd

    for w in (10, 30, 60, 120):
        buy, sell = _roll_mean(b.buy_volume, w), _roll_mean(b.sell_volume, w)
        tot = buy + sell
        imb = np.where(tot > 0, (buy - sell) / np.maximum(tot, 1e-12), 0.0)
        out[f"ofi{w}"] = imb > 0.2
        out[f"ofineg{w}"] = imb < -0.2

    for fast, slow in ((5, 30), (10, 60), (30, 120)):
        out[f"ema{fast}x{slow}"] = _ema(b.close, fast) > _ema(b.close, slow)

    for w in (10, 30, 60):
        out[f"busy{w}"] = _roll_mean(b.trades, w) > 1.5 * _roll_mean(b.trades, 300)

    return out


def resample(b: Bars, k: int) -> Bars:
    """Aggregate k consecutive bars into one.

    1-minute resolution is what you would choose to study a two-minute trade.
    For a position held one to eight hours it is five times more data than the
    decision needs, and it invites scalping-shaped parameters -- which is how
    the first version of this search ended up measuring scalps while calling
    them intraday. Coarser bars make the timeframe explicit.
    """
    if k <= 1:
        return b
    n = (len(b) // k) * k
    if n == 0:
        return b

    def blocks(a: np.ndarray) -> np.ndarray:
        return a[:n].reshape(-1, k)

    return Bars(
        ts=blocks(b.ts)[:, 0],
        open=blocks(b.open)[:, 0],
        high=blocks(b.high).max(axis=1),
        low=blocks(b.low).min(axis=1),
        close=blocks(b.close)[:, -1],
        volume=blocks(b.volume).sum(axis=1),
        buy_volume=blocks(b.buy_volume).sum(axis=1),
        sell_volume=blocks(b.sell_volume).sum(axis=1),
        trades=blocks(b.trades).sum(axis=1),
    )


def _regimes(b: Bars) -> Dict[str, np.ndarray]:
    """Optional context conditions -- when the strategy is allowed to trade."""
    ret1 = np.diff(b.close, prepend=b.close[0]) / b.close
    v = _roll_std(ret1, 60)
    vm = _roll_mean(v, 600)
    vol_hi = v > vm
    liq = _roll_mean(b.volume, 60) > _roll_mean(b.volume, 600)
    return {
        "any": np.ones(len(b), dtype=bool),
        "vol_high": vol_hi,
        "vol_low": ~vol_hi,
        "liquid": liq,
    }


# ---- random strategy ----------------------------------------------------


class Strategy:
    def __init__(self, spec: Dict[str, Any]) -> None:
        self.spec = spec

    @property
    def name(self) -> str:
        s = self.spec
        return (
            f"{'+'.join(s['conds'])}|{s['regime']}|"
            f"tp{s['tp']:g}/sl{s['sl']:g}/{s['hold_min']}m"
        )

    def mask(
        self, preds: Dict[str, np.ndarray], regs: Dict[str, np.ndarray]
    ) -> np.ndarray:
        m = np.ones(len(regs["any"]), dtype=bool)
        for c in self.spec["conds"]:
            m &= np.nan_to_num(preds[c], nan=0).astype(bool)
        return m & regs[self.spec["regime"]]


def sample_strategies(
    rng: np.random.Generator, pred_names: List[str], reg_names: List[str], n: int
) -> List[Strategy]:
    """Draw n distinct strategies from the space."""
    seen, out = set(), []
    tries = 0
    while len(out) < n and tries < n * 50:
        tries += 1
        k = int(rng.choice([1, 2, 3], p=[0.35, 0.45, 0.20]))
        conds = tuple(sorted(rng.choice(pred_names, size=k, replace=False)))
        spec = {
            "conds": list(conds),
            "regime": str(rng.choice(reg_names, p=[0.55, 0.15, 0.15, 0.15])),
            # Intraday geometry: hours, not seconds.
            #
            # The first version of this grid allowed sl down to 10bps, which
            # silently turned every strategy into a scalp: `hold` is only a
            # maximum, so a 10bps stop exits in seconds no matter what it
            # says. It produced entries like tp150/sl10/240m at an 11.7% win
            # rate and 35 trades a day -- labelled intraday, measured as
            # scalping. The stop floor is what makes a trade last.
            #
            # The ceiling matters too: tp150 was the old maximum and it
            # dominated the leaders, which is the signature of a grid
            # truncated below where the answer lives.
            "hold_min": int(rng.choice([60, 120, 240, 360, 480])),
            "tp": float(rng.choice([100, 150, 200, 300, 400, 600])),
            "sl": float(rng.choice([50, 75, 100, 150, 200])),
        }
        key = (conds, spec["regime"], spec["hold_min"], spec["tp"], spec["sl"])
        if key in seen:
            continue
        seen.add(key)
        out.append(Strategy(spec))
    return out


# ---- evaluation ---------------------------------------------------------


def evaluate_strategy(
    s: Strategy,
    bars: Bars,
    preds,
    regs,
    outcome_cache: Dict[Tuple[float, float, int], Any],
    bar_minutes: int,
) -> Optional[Dict[str, float]]:
    hold_bars = max(1, s.spec["hold_min"] // bar_minutes)
    key = (s.spec["tp"], s.spec["sl"], hold_bars)
    if key not in outcome_cache:
        outcome_cache[key] = forward_outcomes(
            bars, s.spec["tp"], s.spec["sl"], hold_bars, 0.0
        )
    gross, held = outcome_cache[key]
    mask = s.mask(preds, regs)
    r = evaluate(mask, gross, held, 1)
    if r["trades"] < 1:
        return None

    # How long the trades *actually* lasted, not the configured maximum.
    # `hold` is a ceiling; a tight stop exits long before it, so a strategy
    # can carry an intraday label while behaving like a scalp. Measuring it
    # is the only way to tell the difference.
    idx = np.where(mask & np.isfinite(gross))[0]
    picked, busy = [], -1
    for i in idx:
        if i > busy:
            picked.append(i)
            busy = i + max(int(held[i]), 1)
    median_hold = float(np.median(held[picked])) * bar_minutes if picked else 0.0

    return {
        "trades": r["trades"],
        "gross_bps": r["mean_bps"],
        "win_rate": r["win_rate"],
        "median_hold_min": median_hold,
    }


def null_best_of(
    gross: np.ndarray,
    held: np.ndarray,
    counts: List[int],
    n_strategies: int,
    draws: int,
    rng,
) -> float:
    """Best-of-N gross edge reachable by entering at random."""
    finite = np.where(np.isfinite(gross))[0]
    bests = []
    for _ in range(draws):
        best = -np.inf
        for _ in range(n_strategies):
            k = counts[rng.integers(len(counts))]
            if k <= 0:
                continue
            pick = finite[rng.integers(0, len(finite), size=min(k, len(finite)))]
            best = max(best, float(gross[pick].mean()))
        if np.isfinite(best):
            bests.append(best)
    return float(np.percentile(bests, 95)) if bests else 0.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--train", default="2024-01")
    p.add_argument("--train-end", default="2025-08")
    p.add_argument("--test", default="2025-09")
    p.add_argument("--test-end", default="2026-07")
    p.add_argument(
        "--bar-minutes",
        type=int,
        default=5,
        help="Resample the 1m cache to this granularity",
    )
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--seed", type=int, default=20260901)
    p.add_argument("--null-draws", type=int, default=40)
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args(argv)

    cache = Path(args.data) / "bars"
    train, n_tr = load_months(
        cache, args.symbol, args.timeframe, args.train, args.train_end
    )
    test, n_te = load_months(
        cache, args.symbol, args.timeframe, args.test, args.test_end
    )
    train = resample(train, args.bar_minutes)
    test = resample(test, args.bar_minutes)
    print(
        f"{args.symbol}: train {len(train):,} x {args.bar_minutes}m bars "
        f"({n_tr} months), test {len(test):,} ({n_te} months)"
    )

    preds_tr, regs_tr = _predicates(train), _regimes(train)
    preds_te, regs_te = _predicates(test), _regimes(test)
    rng = np.random.default_rng(args.seed)
    strategies = sample_strategies(rng, sorted(preds_tr), list(regs_tr), args.n)
    print(
        f"{len(strategies)} random strategies from "
        f"{len(preds_tr)} predicates x {len(regs_tr)} regimes\n"
    )

    print("filter fixed before running:")
    print(f"  Tier 0  >= {MIN_TRADES} non-overlapping trades")
    print(f"  Tier 1  gross edge > {COST_MAKER_BPS:g}bps round trip (all-maker)")
    print("  Tier 2  net edge > best-of-100 null threshold")
    print("  Tier 3  net edge still positive out of sample")
    print(f"  Tier 4  still positive at {COST_TAKER_BPS:g}bps taker cost\n")

    cache_tr: Dict[Tuple[float, float, int], Any] = {}
    rows = []
    for s in strategies:
        r = evaluate_strategy(s, train, preds_tr, regs_tr, cache_tr, args.bar_minutes)
        if r:
            rows.append((s, r))
    print(f"evaluated {len(rows)}/{len(strategies)} (rest produced no trades)")

    t0 = [(s, r) for s, r in rows if r["trades"] >= MIN_TRADES]
    print(f"\nTier 0  >= {MIN_TRADES} trades          : {len(t0)}/{len(rows)} pass")
    if t0:
        holds = sorted(r["median_hold_min"] for _, r in t0)
        per_day = sorted(r["trades"] / (n_tr * 30.4) for _, r in t0)
        mid = len(holds) // 2
        print(
            f"        actual median hold      : {holds[mid]:.0f} min "
            f"(range {holds[0]:.0f}-{holds[-1]:.0f})"
        )
        print(
            f"        trades per day          : {per_day[mid]:.1f} "
            f"(range {per_day[0]:.1f}-{per_day[-1]:.1f})"
        )

    t1 = [
        (s, r) for s, r in t0 if r["gross_bps"] > COST_MAKER_BPS + REQUIRED_MARGIN_BPS
    ]
    print(f"Tier 1  gross > {COST_MAKER_BPS:g}bps cost      : {len(t1)}/{len(t0)} pass")

    if t1:
        # The null has to be built from the strategy's *own* geometry and its
        # own trade count. Per-trade dispersion depends heavily on tp/sl/hold,
        # so a tp600/sl200 strategy and a tp100/sl50 one cannot share a
        # threshold -- an earlier version drew one from whichever geometry
        # happened to be cached first and applied it to all of them, which
        # made the bar arbitrary in exactly the tier that rejects everything.
        t2, thrs = [], []
        for s, r in t1:
            hold_bars = max(1, s.spec["hold_min"] // args.bar_minutes)
            g, h = cache_tr[(s.spec["tp"], s.spec["sl"], hold_bars)]
            thr_s = null_best_of(
                g, h, [int(r["trades"])], len(rows), args.null_draws, rng
            )
            thrs.append(thr_s)
            if r["gross_bps"] > thr_s:
                t2.append((s, r))
        thr = float(np.median(thrs)) if thrs else float("nan")
        print(f"Tier 2  beats own null (med {thr:+.1f}bps): {len(t2)}/{len(t1)} pass")
        for (s, r), t in zip(t1, thrs):
            flag = "PASS" if r["gross_bps"] > t else "    "
            print(
                f"        {flag} {s.name[:44]:44} {r['gross_bps']:+7.2f} vs "
                f"null {t:+7.2f}"
            )
    else:
        t2, thr = [], float("nan")
        print("Tier 2  beats null              : skipped, nothing reached it")

    t3 = []
    cache_te: Dict[Tuple[float, float, int], Any] = {}
    for s, r in t2:
        rt = evaluate_strategy(s, test, preds_te, regs_te, cache_te, args.bar_minutes)
        if rt and rt["gross_bps"] - COST_MAKER_BPS > 0:
            t3.append((s, r, rt))
    print(f"Tier 3  holds out of sample     : {len(t3)}/{len(t2)} pass")

    t4 = [(s, r, rt) for s, r, rt in t3 if rt["gross_bps"] - COST_TAKER_BPS > 0]
    print(f"Tier 4  survives taker cost     : {len(t4)}/{len(t3)} pass\n")

    # Ranked among Tier 0 survivors only. A 6-trade strategy showing +24bps is
    # exactly the noise Tier 0 exists to remove, and quoting it as "the best
    # result" would misrepresent the search to anyone reading the summary.
    rows = sorted(t0, key=lambda kv: -kv[1]["gross_bps"])
    hdr = (
        f"{'#':>2} {'strategy':52} {'trades':>7} {'win':>7} {'gross':>8} "
        f"{'net@4':>8} {'net@14':>8}"
    )
    print(f"top by gross edge among the {len(t0)} with >= {MIN_TRADES} trades:")
    print(hdr)
    print("-" * len(hdr))
    for i, (s, r) in enumerate(rows[: args.top], 1):
        print(
            f"{i:>2} {s.name[:52]:52} {r['trades']:7d} {r['win_rate']:7.1%} "
            f"{r['gross_bps']:+8.2f} {r['gross_bps'] - COST_MAKER_BPS:+8.2f} "
            f"{r['gross_bps'] - COST_TAKER_BPS:+8.2f}"
        )

    survivors = [s.name for s, _, _ in t4]
    print()
    if survivors:
        print(f"{len(survivors)} strategies cleared every tier:")
        for nm in survivors:
            print(f"  {nm}")
    else:
        print("No strategy cleared every tier.")
        if rows:
            best = rows[0][1]["gross_bps"]
            print(
                f"Best gross edge among the {len(rows)} strategies with a "
                f"measurable trade count:\n{best:+.2f}bps per trade, against "
                f"a {COST_MAKER_BPS:g}bps round trip at the most favourable "
                f"execution\navailable. "
                "Ranking the survivors of a filter\nnothing reached would be "
                "reporting noise."
            )
    out = Path("artifacts/random_strategy_search.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "n": len(strategies),
                "evaluated": len(rows),
                "tier0": len(t0),
                "tier1": len(t1),
                "tier2": len(t2),
                "tier3": len(t3),
                "tier4": len(t4),
                "null_threshold_bps": thr,
                "top": [{"name": s.name, **r} for s, r in rows[:20]],
            },
            indent=1,
        )
    )
    return 0 if survivors else 2


if __name__ == "__main__":
    raise SystemExit(main())
