#!/usr/bin/env python
"""Run the final crypto test, per docs/CROSS_SECTIONAL_V2_PREREGISTRATION.md.

    python scripts/cross_sectional_v2.py --cache-dir DIR

Criteria were committed before this file existed. All four must hold:

1. excess over the equal-weight benchmark > 0
2. excess clears the best-of-5 null threshold
3. annualised Sharpe of the excess >= 0.8
4. excess positive in a majority of walk-forward folds

The first cross-sectional test could not distinguish "no edge" from "an edge
too small to see": it ranked over the 103 unflagged names (median 37) and held
the top decile, so a portfolio was frequently **3 names**, and random selection
alone had a 1.74% per-period standard deviation.

The fix is power, not parameters. The full point-in-time universe has a median
of 292 names from 2019-11, where a 20% slice is 58. Nothing about the scoring
is relaxed -- the null, the benchmark and the walk-forward are unchanged.

Ten cells are reported: five signals x two horizons x two universes. The count
is fixed by the pre-registration and the null accounts for the signal count.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

DAY_MS = 86_400_000

# ---- fixed by the pre-registration --------------------------------------
MIN_UNIVERSE = 30  # a period is not scored below this
TOP_FRAC = 0.20  # slice width
MIN_HOLDINGS = 15  # floor on the slice
COST_BPS = 14.0  # taker, on realised turnover
REQUIRED_SHARPE = 0.8
FOLDS = 5


def load_panel(cache_dir: Path, symbols: List[str]) -> Dict[str, dict]:
    panel = {}
    for s in symbols:
        f = cache_dir / f"{s}USDT.npz"
        if f.exists():
            z = np.load(f)
            panel[s] = {"ts": z["ts"], "close": z["close"]}
    return panel


def _px(d, t_ms: int) -> Optional[float]:
    """Last close at or before t. A dead name returns its final print, so a
    holder takes the loss rather than the position silently vanishing."""
    i = np.searchsorted(d["ts"], t_ms, side="right") - 1
    return float(d["close"][i]) if i >= 0 else None


def _series(d, t_ms: int) -> np.ndarray:
    return d["close"][d["ts"] <= t_ms]


def _ret(px: np.ndarray, days: int) -> Optional[float]:
    if len(px) < days + 1:
        return None
    return float(px[-1] / px[-days - 1] - 1.0)


def mom(days: int) -> Callable:
    return lambda d, t: _ret(_series(d, t), days)


def reversal(days: int) -> Callable:
    def f(d, t):
        r = _ret(_series(d, t), days)
        return None if r is None else -r

    return f


def vol_scaled(days: int) -> Callable:
    def f(d, t):
        px = _series(d, t)
        r = _ret(px, days)
        if r is None:
            return None
        rets = np.diff(px[-days - 1 :]) / px[-days - 1 : -1]
        sd = float(np.std(rets))
        return None if sd <= 0 else r / sd

    return f


def downside(days: int) -> Callable:
    def f(d, t):
        px = _series(d, t)
        if len(px) < days + 1:
            return None
        rets = np.diff(px[-days - 1 :]) / px[-days - 1 : -1]
        neg = rets[rets < 0]
        return None if len(neg) < 3 else -float(np.std(neg))

    return f


#: Five, fixed. A sixth added after seeing results would invalidate the null.
SIGNALS: Dict[str, Callable] = {
    "mom_1m": mom(30),
    "mom_3m": mom(90),
    "reversal_1w": reversal(7),
    "mom_3m_volscaled": vol_scaled(90),
    "low_downside_vol": downside(90),
}


def forward_returns(panel, members, t0: int, t1: int) -> Dict[str, float]:
    out = {}
    for s in members:
        d = panel.get(s)
        if d is None:
            continue
        p0, p1 = _px(d, t0), _px(d, t1)
        if p0 and p1 and p0 > 0:
            out[s] = p1 / p0 - 1.0
    return out


def run(
    panel, members_by_date, dates, signal: Optional[str], hold_days: int, rng=None
) -> Tuple[np.ndarray, np.ndarray]:
    """Excess over equal-weight per period. signal=None picks at random."""
    fn = SIGNALS[signal] if signal else None
    excess, bench, held = [], [], set()
    for t0, t1 in zip(dates, dates[1:]):
        snap = max((d for d in members_by_date if d <= t0), default=None)
        if snap is None:
            continue
        members = members_by_date[snap]
        if len(members) < MIN_UNIVERSE:
            continue
        rets = forward_returns(panel, members, t0, t1)
        if len(rets) < MIN_UNIVERSE:
            continue
        k = max(MIN_HOLDINGS, int(len(rets) * TOP_FRAC))
        k = min(k, len(rets))
        if fn is None:
            picks = set(rng.choice(list(rets), size=k, replace=False))
        else:
            scored = []
            for s in rets:
                v = fn(panel[s], t0)
                if v is not None and np.isfinite(v):
                    scored.append((v, s))
            if len(scored) < MIN_UNIVERSE:
                continue
            scored.sort(reverse=True)
            picks = {s for _, s in scored[:k]}
        got = [rets[s] for s in picks if s in rets]
        if not got:
            continue
        turn = len(picks - held) / max(1, len(picks))
        net = float(np.mean(got)) - turn * (COST_BPS / 1e4)
        b = float(np.mean(list(rets.values())))
        excess.append(net - b)
        bench.append(b)
        held = picks
    return np.array(excess), np.array(bench)


def null_threshold(panel, mbd, dates, hold_days, n_signals, draws, rng) -> float:
    bests = []
    for _ in range(draws):
        best = -np.inf
        for _ in range(n_signals):
            e, _b = run(panel, mbd, dates, None, hold_days, rng)
            if e.size:
                best = max(best, float(e.mean()))
        if np.isfinite(best):
            bests.append(best)
    return float(np.percentile(bests, 95)) if bests else 0.0


def sharpe(x: np.ndarray, per_year: float) -> float:
    if x.size < 2:
        return 0.0
    sd = float(np.std(x, ddof=1))
    return 0.0 if sd <= 0 else float(np.mean(x)) / sd * np.sqrt(per_year)


def walk_forward(panel, mbd, dates, hold_days) -> Tuple[int, int]:
    """Pick the best signal on each fold, trade it on the next."""
    b = np.linspace(0, len(dates), FOLDS + 1, dtype=int)
    pos = tot = 0
    for i in range(FOLDS - 1):
        tr, te = dates[b[i] : b[i + 1] + 1], dates[b[i + 1] : b[i + 2] + 1]
        if len(tr) < 3 or len(te) < 3:
            continue
        best, bv = None, -np.inf
        for nm in SIGNALS:
            e, _ = run(panel, mbd, tr, nm, hold_days)
            if e.size and float(e.mean()) > bv:
                best, bv = nm, float(e.mean())
        if best is None:
            continue
        e, _ = run(panel, mbd, te, best, hold_days)
        if e.size:
            tot += 1
            pos += int(e.mean() > 0)
    return pos, tot


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-dir", required=True)
    p.add_argument("--timeline", default="docs/universe_timeline.json")
    p.add_argument("--candidates", default="docs/universe_candidates.json")
    p.add_argument("--start", default="2019-11-05")
    p.add_argument("--null-draws", type=int, default=30)
    args = p.parse_args(argv)

    tl = json.loads(Path(args.timeline).read_text())
    cands = json.loads(Path(args.candidates).read_text())
    unflagged = {c["base"] for c in cands if c["known"] and not c["flags"]}
    start_ms = int(
        datetime.strptime(args.start, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )

    full_mbd, unf_mbd = {}, {}
    for ds, names in tl.items():
        t = int(
            datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            * 1000
        )
        if t < start_ms:
            continue
        full_mbd[t] = names
        keep = [n for n in names if n in unflagged]
        if keep:
            unf_mbd[t] = keep

    universe = sorted({n for v in full_mbd.values() for n in v})
    panel = load_panel(Path(args.cache_dir), universe)
    print("final crypto test, per docs/CROSS_SECTIONAL_V2_PREREGISTRATION.md")
    print(f"{len(panel)} instruments with data, from {args.start}")
    fs = [len(v) for v in full_mbd.values()]
    us = [len(v) for v in unf_mbd.values()]
    print(
        f"  full universe     : {len(full_mbd)} snapshots, median {int(np.median(fs))}"
    )
    print(
        f"  unflagged subset  : {len(unf_mbd)} snapshots, median {int(np.median(us))}"
    )
    print(
        f"  slice: top {TOP_FRAC:.0%}, floor {MIN_HOLDINGS} names, "
        f"period floor {MIN_UNIVERSE}\n"
    )

    rng = np.random.default_rng(2026)
    results = {}
    for uname, mbd in (("full", full_mbd), ("unflagged", unf_mbd)):
        for hold in (30, 90):
            lo, hi = min(mbd), max(mbd)
            dates = list(range(lo, hi, hold * DAY_MS))
            if len(dates) < 8:
                continue
            per_year = 365.25 / hold
            thr = null_threshold(
                panel, mbd, dates, hold, len(SIGNALS), args.null_draws, rng
            )
            print(f"=== {uname} universe, {hold}-day hold ({len(dates)} periods) ===")
            print(f"  best-of-{len(SIGNALS)} null: {thr * 100:+.3f}% per period")
            hdr = (
                f"  {'signal':20} {'periods':>8} {'excess/pd':>11} "
                f"{'ann.':>9} {'Sharpe':>8}  verdict"
            )
            print(hdr)
            print("  " + "-" * (len(hdr) - 2))
            best = None
            for nm in SIGNALS:
                e, b = run(panel, mbd, dates, nm, hold)
                if not e.size:
                    print(f"  {nm:20} {'-':>8}  insufficient data")
                    continue
                m, sh = float(e.mean()), sharpe(e, per_year)
                v = "clears null" if m > thr else "within noise"
                if best is None or m > best[1]:
                    best = (nm, m, sh)
                print(
                    f"  {nm:20} {e.size:>8} {m * 100:>+10.3f}% "
                    f"{m * per_year * 100:>+8.2f}% {sh:>8.2f}  {v}"
                )
            pos, tot = walk_forward(panel, mbd, dates, hold)
            print(f"  walk-forward: positive in {pos}/{tot} folds")
            if best:
                nm, m, sh = best
                c = [m > 0, m > thr, sh >= REQUIRED_SHARPE, tot > 0 and pos > tot / 2]
                results[f"{uname}_{hold}d"] = {
                    "best": nm,
                    "excess": float(m),
                    "sharpe": float(sh),
                    "null": float(thr),
                    "folds": f"{pos}/{tot}",
                    "criteria": [bool(x) for x in c],
                    "pass": bool(all(c)),
                }
                print(
                    f"  criteria: "
                    f"{'PASS' if c[0] else 'FAIL'} excess>0  "
                    f"{'PASS' if c[1] else 'FAIL'} beats null  "
                    f"{'PASS' if c[2] else 'FAIL'} Sharpe>={REQUIRED_SHARPE}  "
                    f"{'PASS' if c[3] else 'FAIL'} majority folds"
                )
            print()

    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/cross_sectional_v2.json").write_text(json.dumps(results, indent=1))
    passed = [k for k, v in results.items() if v["pass"]]
    print("=" * 72)
    if passed:
        print(f"RESULT: {len(passed)} cell(s) met every criterion: {', '.join(passed)}")
        print("The pre-registration calls for forward paper measurement next,")
        print("not deployment.")
    else:
        print("RESULT: negative. No cell met all four criteria.")
        print("Per the pre-registration this is the last crypto test; the")
        print("search stops here regardless of what any single cell showed.")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
