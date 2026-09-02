#!/usr/bin/env python
"""Test CTREND, the trend factor from the crypto cross-section literature.

    python scripts/ctrend_test.py --cache-dir DIR

*A Trend Factor for the Cross Section of Cryptocurrency Returns* (JFQA)
reports a factor aggregating price and volume information across horizons
that "survives the impact of transaction costs and persists in big and liquid
coins". That last clause is why it is worth testing here: every other
documented crypto anomaly draws its alpha from micro-caps representing under
0.3% of market capitalisation, which is below the liquidity band where our own
measurement puts the move-to-cost ratio at 1.3x.

**What makes this different from the ~400 variants already tested.** Those were
fixed rules -- a signal with a threshold. CTREND is a *learned* combination:
moving averages at many horizons, weighted by coefficients estimated from the
cross-section. Following Han, Zhou and Zhu (2016), whose construction the
crypto paper follows:

1. at each date, for each coin, compute MA(price, L) / price for several L,
   and the same for volume -- normalising makes them comparable across coins
2. for every *past* period, cross-sectionally regress the realised forward
   return on those signals
3. average those coefficients over history to get expected-return weights
4. forecast each coin's return, rank, hold the top slice

Step 2 uses only periods that have already resolved, so the weights at any
rebalance date are estimated from data available then. That is the whole
difficulty of implementing this honestly: the factor learns, and a learned
factor is trivially made to look good by fitting it on the same data it is
scored on.

**Criteria, fixed before this ran** -- the same four used throughout:

1. excess over the equal-weight benchmark > 0
2. excess clears the best-of-N null threshold
3. annualised Sharpe of the excess >= 0.8
4. excess positive in a majority of walk-forward folds

Two universes are reported. The full point-in-time universe gives maximum
power. The **liquid subset** -- top 50 by trailing volume at each date -- tests
the paper's actual claim, which is about big and liquid coins rather than the
whole cross-section.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

DAY_MS = 86_400_000

# ---- fixed before running ------------------------------------------------
#: Lookback horizons in days, as in the trend-factor literature.
HORIZONS = (3, 5, 10, 20, 50, 100, 200)
TOP_FRAC = 0.20
MIN_HOLDINGS = 15
MIN_UNIVERSE = 30
COST_BPS = 14.0
REQUIRED_SHARPE = 0.8
FOLDS = 5
LIQUID_TOP_N = 50  # "big and liquid coins"
MIN_TRAIN_PERIODS = 12  # before this, no coefficients are estimated


def load_panel(cache_dir: Path, symbols: List[str]) -> Dict[str, dict]:
    panel = {}
    for s in symbols:
        f = cache_dir / f"{s}USDT.npz"
        if f.exists():
            z = np.load(f)
            panel[s] = {"ts": z["ts"], "close": z["close"], "qvol": z["qvol"]}
    return panel


def _idx(d, t_ms: int) -> int:
    return int(np.searchsorted(d["ts"], t_ms, side="right") - 1)


def signals_at(d, t_ms: int) -> Optional[np.ndarray]:
    """Normalised price and volume moving averages, as of t.

    Dividing by the current level is what makes these comparable across coins
    priced at $0.0001 and $60,000 -- without it the regression would be
    dominated by price level rather than trend.
    """
    i = _idx(d, t_ms)
    if i < HORIZONS[-1]:
        return None
    px, vol = d["close"], d["qvol"]
    p0 = px[i]
    if p0 <= 0:
        return None
    out = []
    for L in HORIZONS:
        w = px[i - L + 1 : i + 1]
        out.append(float(w.mean() / p0) - 1.0)
    v0 = float(vol[max(0, i - 19) : i + 1].mean())
    for L in HORIZONS:
        w = vol[i - L + 1 : i + 1]
        out.append(float(w.mean() / v0) - 1.0 if v0 > 0 else 0.0)
    a = np.asarray(out, dtype=float)
    return a if np.all(np.isfinite(a)) else None


def fwd_return(d, t0: int, t1: int) -> Optional[float]:
    i, j = _idx(d, t0), _idx(d, t1)
    if i < 0 or j < 0:
        return None
    p0, p1 = float(d["close"][i]), float(d["close"][j])
    return p1 / p0 - 1.0 if p0 > 0 else None


def period_cross_section(panel, members, t0: int, t1: int):
    """Signals at t0 and the return they must predict, for one period."""
    X, y, names = [], [], []
    for s in members:
        d = panel.get(s)
        if d is None:
            continue
        x = signals_at(d, t0)
        r = fwd_return(d, t0, t1)
        if x is None or r is None or not np.isfinite(r):
            continue
        X.append(x)
        y.append(r)
        names.append(s)
    if len(names) < MIN_UNIVERSE:
        return None
    return np.asarray(X), np.asarray(y), names


def fit_weights(history) -> Optional[np.ndarray]:
    """Average of past cross-sectional regression coefficients.

    Each past period contributes one coefficient vector; the forecast weights
    are their mean. Only resolved periods are used, so nothing here sees the
    period being predicted.
    """
    betas = []
    for X, y in history:
        Xs = (X - X.mean(0)) / (X.std(0) + 1e-12)
        try:
            b, *_ = np.linalg.lstsq(
                np.column_stack([np.ones(len(Xs)), Xs]), y, rcond=None
            )
        except np.linalg.LinAlgError:
            continue
        if np.all(np.isfinite(b)):
            betas.append(b[1:])
    return np.mean(betas, axis=0) if len(betas) >= MIN_TRAIN_PERIODS else None


def run_ctrend(
    panel, members_by_date, dates, cost_bps: float, rng=None, randomise: bool = False
):
    """Excess over equal-weight per period, using expanding-window weights."""
    history: List[Tuple[np.ndarray, np.ndarray]] = []
    excess, bench, held = [], [], set()
    for t0, t1 in zip(dates, dates[1:]):
        snap = max((d for d in members_by_date if d <= t0), default=None)
        if snap is None:
            continue
        cs = period_cross_section(panel, members_by_date[snap], t0, t1)
        if cs is None:
            continue
        X, y, names = cs
        w = fit_weights(history)
        # The period is added to history only after it has been used for
        # scoring, so its own outcome never informs its own forecast.
        history.append((X, y))
        if w is None:
            continue
        k = min(max(MIN_HOLDINGS, int(len(names) * TOP_FRAC)), len(names))
        if randomise:
            picks = set(rng.choice(names, size=k, replace=False))
        else:
            Xs = (X - X.mean(0)) / (X.std(0) + 1e-12)
            fc = Xs @ w
            order = np.argsort(-fc)
            picks = {names[i] for i in order[:k]}
        got = [y[i] for i, s in enumerate(names) if s in picks]
        if not got:
            continue
        turn = len(picks - held) / max(1, len(picks))
        net = float(np.mean(got)) - turn * (cost_bps / 1e4)
        b = float(np.mean(y))
        excess.append(net - b)
        bench.append(b)
        held = picks
    return np.array(excess), np.array(bench)


def sharpe(x: np.ndarray, per_year: float) -> float:
    if x.size < 2:
        return 0.0
    sd = float(np.std(x, ddof=1))
    return 0.0 if sd <= 0 else float(np.mean(x)) / sd * np.sqrt(per_year)


def liquid_subset(panel, members: List[str], t_ms: int, n: int) -> List[str]:
    """Top n by trailing 30-day volume as of t -- judged then, not today."""
    scored = []
    for s in members:
        d = panel.get(s)
        if d is None:
            continue
        i = _idx(d, t_ms)
        if i < 30:
            continue
        v = float(np.median(d["qvol"][i - 29 : i + 1]))
        if v > 0:
            scored.append((v, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:n]]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-dir", required=True)
    p.add_argument("--timeline", default="docs/universe_timeline.json")
    p.add_argument("--start", default="2019-11-05")
    p.add_argument("--null-draws", type=int, default=15)
    args = p.parse_args(argv)

    tl = json.loads(Path(args.timeline).read_text())
    start = int(
        datetime.strptime(args.start, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )
    mbd = {}
    for ds, names in tl.items():
        t = int(
            datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            * 1000
        )
        if t >= start:
            mbd[t] = names
    panel = load_panel(
        Path(args.cache_dir), sorted({n for v in mbd.values() for n in v})
    )
    print("CTREND -- trend factor from price and volume, learned weights")
    print(
        f"{len(panel)} instruments, {len(HORIZONS)} price + "
        f"{len(HORIZONS)} volume horizons, from {args.start}"
    )
    print(
        f"weights: mean of past cross-sectional regressions, "
        f"min {MIN_TRAIN_PERIODS} periods before trading\n"
    )

    rng = np.random.default_rng(4242)
    results = {}
    for uname in ("full", "liquid50"):
        if uname == "liquid50":
            u = {t: liquid_subset(panel, v, t, LIQUID_TOP_N) for t, v in mbd.items()}
            u = {t: v for t, v in u.items() if len(v) >= MIN_UNIVERSE}
        else:
            u = mbd
        if not u:
            continue
        for hold in (7, 30):
            lo, hi = min(u), max(u)
            dates = list(range(lo, hi, hold * DAY_MS))
            if len(dates) < 20:
                continue
            per_year = 365.25 / hold
            e, b = run_ctrend(panel, u, dates, COST_BPS)
            if not e.size:
                print(f"=== {uname}, {hold}d: insufficient data ===\n")
                continue
            nulls = []
            for _ in range(args.null_draws):
                en, _bn = run_ctrend(panel, u, dates, COST_BPS, rng, True)
                if en.size:
                    nulls.append(float(en.mean()))
            thr = float(np.percentile(nulls, 95)) if nulls else 0.0

            # walk-forward: the weights already expand, so folds test stability
            bnd = np.linspace(0, len(dates), FOLDS + 1, dtype=int)
            pos = tot = 0
            for i in range(FOLDS):
                seg = dates[bnd[i] : bnd[i + 1] + 1]
                if len(seg) < 5:
                    continue
                es, _ = run_ctrend(panel, u, seg, COST_BPS)
                if es.size:
                    tot += 1
                    pos += int(es.mean() > 0)

            m, sh = float(e.mean()), sharpe(e, per_year)
            c = [m > 0, m > thr, sh >= REQUIRED_SHARPE, tot > 0 and pos > tot / 2]
            results[f"{uname}_{hold}d"] = {
                "periods": int(e.size),
                "excess": m,
                "sharpe": sh,
                "null": thr,
                "benchmark": float(b.mean()),
                "folds": f"{pos}/{tot}",
                "criteria": [bool(x) for x in c],
                "pass": bool(all(c)),
            }
            print(
                f"=== {uname} universe, {hold}-day rebalance "
                f"({e.size} scored periods) ==="
            )
            print(
                f"  excess over equal-weight : {m * 100:+.3f}% per period "
                f"({m * per_year * 100:+.2f}% annualised)"
            )
            print(f"  benchmark itself         : {b.mean() * 100:+.3f}% per period")
            print(f"  best-of-N null           : {thr * 100:+.3f}%")
            print(f"  Sharpe                   : {sh:.2f}")
            print(f"  walk-forward             : positive in {pos}/{tot} folds")
            print(
                f"  criteria: {'PASS' if c[0] else 'FAIL'} excess>0  "
                f"{'PASS' if c[1] else 'FAIL'} beats null  "
                f"{'PASS' if c[2] else 'FAIL'} Sharpe>={REQUIRED_SHARPE}  "
                f"{'PASS' if c[3] else 'FAIL'} majority folds"
            )
            print()

    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/ctrend_test.json").write_text(json.dumps(results, indent=1))
    passed = [k for k, v in results.items() if v["pass"]]
    print("=" * 72)
    print(
        f"RESULT: {'PASS in ' + ', '.join(passed) if passed else 'negative'}"
        f" -- {len(passed)}/{len(results)} cells met all four criteria"
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
