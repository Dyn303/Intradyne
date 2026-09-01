#!/usr/bin/env python
"""Run the pre-registered cross-sectional test.

    python scripts/cross_sectional_test.py --cache-dir DIR

Criteria are fixed in ``docs/CROSS_SECTIONAL_PREREGISTRATION.md``, committed
before this script was written. All four must hold:

1. excess over the equal-weight benchmark > 0
2. excess clears the best-of-N null threshold
3. annualised Sharpe of the excess >= 0.8
4. excess positive in a majority of walk-forward folds

Everything measured before this asked *when to enter one instrument*. This
asks *which instrument to hold*: rank the universe, hold the top slice,
rebalance monthly. At a monthly hold a 14bps round trip is a rounding error,
where at a two-minute horizon it was 28x the measured edge.

Three things carry most of the honesty here.

**Excess, not absolute.** Over months a long-only rule in crypto earns
whatever the market did, which dwarfs costs, so beating zero proves nothing.
Every number is excess over holding the whole universe equal-weighted, which
carries identical drift *and* identical survivorship composition.

**Dead names are held to the end.** A coin that stops trading is not dropped
from the month it died in. Its return is computed to its last traded price, so
a portfolio holding it takes the loss. This is still mildly optimistic --
it assumes you sold at the last print rather than being stuck in a halted
market -- and that is noted in the output rather than hidden.

**The null is best-of-N, not zero.** Eight signals are tested, so the best of
them looks good by chance. Random portfolios with matched size and turnover
give the distribution of "best of eight under no edge"; the 95th percentile is
the bar a signal must clear.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

DAY_MS = 86_400_000


# --------------------------------------------------------------------------
# Panel access
# --------------------------------------------------------------------------


def load_panel(cache_dir: Path, symbols: List[str]) -> Dict[str, Dict[str, np.ndarray]]:
    panel = {}
    for s in symbols:
        f = cache_dir / f"{s}USDT.npz"
        if f.exists():
            z = np.load(f)
            panel[s] = {"ts": z["ts"], "close": z["close"], "qvol": z["qvol"]}
    return panel


def price_at(d: Dict[str, np.ndarray], t_ms: int) -> Optional[float]:
    """Last close at or before ``t_ms``.

    For a name that has stopped trading this returns its final print, which
    is what a holder would have realised. Returning None instead would
    silently drop the position and hide the loss.
    """
    ts = d["ts"]
    i = np.searchsorted(ts, t_ms, side="right") - 1
    if i < 0:
        return None
    return float(d["close"][i])


def series_upto(d: Dict[str, np.ndarray], t_ms: int) -> np.ndarray:
    return d["close"][d["ts"] <= t_ms]


# --------------------------------------------------------------------------
# Signals -- every one reads only data at or before the rebalance date
# --------------------------------------------------------------------------


def _ret(px: np.ndarray, days: int) -> Optional[float]:
    if len(px) < days + 1:
        return None
    return float(px[-1] / px[-days - 1] - 1.0)


def mom(days: int) -> Callable:
    def f(d, t):
        return _ret(series_upto(d, t), days)

    return f


def reversal(days: int) -> Callable:
    def f(d, t):
        r = _ret(series_upto(d, t), days)
        return None if r is None else -r

    return f


def vol_scaled_mom(days: int) -> Callable:
    def f(d, t):
        px = series_upto(d, t)
        r = _ret(px, days)
        if r is None:
            return None
        rets = np.diff(px[-days - 1 :]) / px[-days - 1 : -1]
        sd = float(np.std(rets))
        return None if sd <= 0 else r / sd

    return f


def downside_vol(days: int) -> Callable:
    """Rank by *low* downside deviation, so it is a defensive signal."""

    def f(d, t):
        px = series_upto(d, t)
        if len(px) < days + 1:
            return None
        rets = np.diff(px[-days - 1 :]) / px[-days - 1 : -1]
        neg = rets[rets < 0]
        if len(neg) < 3:
            return None
        return -float(np.std(neg))

    return f


def volume_trend(days: int) -> Callable:
    def f(d, t):
        m = d["ts"] <= t
        v = d["qvol"][m]
        if len(v) < 2 * days:
            return None
        recent = float(np.median(v[-days:]))
        prior = float(np.median(v[-2 * days : -days]))
        return None if prior <= 0 else recent / prior - 1.0

    return f


SIGNALS: Dict[str, Callable] = {
    "mom_1m": mom(30),
    "mom_3m": mom(90),
    "mom_6m": mom(180),
    "mom_12m": mom(365),
    "reversal_1w": reversal(7),
    "mom_3m_volscaled": vol_scaled_mom(90),
    "low_downside_vol": downside_vol(90),
    "volume_trend": volume_trend(30),
}


# --------------------------------------------------------------------------
# Portfolio simulation
# --------------------------------------------------------------------------


def forward_returns(panel, members: List[str], t0: int, t1: int) -> Dict[str, float]:
    """Return of each member from t0 to t1, dead names included."""
    out = {}
    for s in members:
        d = panel.get(s)
        if d is None:
            continue
        p0, p1 = price_at(d, t0), price_at(d, t1)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        out[s] = p1 / p0 - 1.0
    return out


def run_signal(
    panel, dates, members_by_date, name: str, top_frac: float, cost_bps: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Excess-over-benchmark per period, plus turnover and benchmark."""
    fn = SIGNALS[name]
    excess, turnovers, bench = [], [], []
    held: set = set()
    for t0, t1 in zip(dates, dates[1:]):
        members = members_by_date.get(t0, [])
        if len(members) < 10:
            continue
        scored = []
        for s in members:
            d = panel.get(s)
            if d is None:
                continue
            v = fn(d, t0)
            if v is not None and np.isfinite(v):
                scored.append((v, s))
        if len(scored) < 10:
            continue
        scored.sort(reverse=True)
        k = max(3, int(len(scored) * top_frac))
        picks = {s for _, s in scored[:k]}

        rets = forward_returns(panel, members, t0, t1)
        if not rets:
            continue
        held_rets = [rets[s] for s in picks if s in rets]
        if not held_rets:
            continue
        # Turnover is the fraction of the book replaced, and both legs cost.
        turn = len(picks - held) / max(1, len(picks))
        gross = float(np.mean(held_rets))
        net = gross - turn * (cost_bps / 1e4)
        b = float(np.mean(list(rets.values())))
        excess.append(net - b)
        turnovers.append(turn)
        bench.append(b)
        held = picks
    return np.array(excess), np.array(turnovers), np.array(bench)


def random_null(
    panel,
    dates,
    members_by_date,
    k_frac: float,
    cost_bps: float,
    n_signals: int,
    draws: int,
    rng,
) -> float:
    """95th percentile of best-of-N excess under random selection."""
    bests = []
    for _ in range(draws):
        best = -np.inf
        for _ in range(n_signals):
            ex = []
            held: set = set()
            for t0, t1 in zip(dates, dates[1:]):
                members = members_by_date.get(t0, [])
                if len(members) < 10:
                    continue
                rets = forward_returns(panel, members, t0, t1)
                if len(rets) < 10:
                    continue
                names = list(rets)
                k = max(3, int(len(names) * k_frac))
                picks = set(rng.choice(names, size=min(k, len(names)), replace=False))
                turn = len(picks - held) / max(1, len(picks))
                net = float(np.mean([rets[s] for s in picks])) - turn * (cost_bps / 1e4)
                ex.append(net - float(np.mean(list(rets.values()))))
                held = picks
            if ex:
                best = max(best, float(np.mean(ex)))
        if np.isfinite(best):
            bests.append(best)
    return float(np.percentile(bests, 95)) if bests else 0.0


def sharpe(x: np.ndarray, periods_per_year: float) -> float:
    if len(x) < 2:
        return 0.0
    sd = float(np.std(x, ddof=1))
    return 0.0 if sd <= 0 else float(np.mean(x)) / sd * np.sqrt(periods_per_year)


def walk_forward(
    panel, dates, members_by_date, top_frac, cost_bps, folds: int
) -> Tuple[List[Tuple[str, float]], float]:
    """Pick the best signal on each fold, trade it on the next."""
    bounds = np.linspace(0, len(dates), folds + 1, dtype=int)
    picks = []
    for i in range(folds - 1):
        tr = dates[bounds[i] : bounds[i + 1] + 1]
        te = dates[bounds[i + 1] : bounds[i + 2] + 1]
        if len(tr) < 3 or len(te) < 3:
            continue
        best, best_v = None, -np.inf
        for nm in SIGNALS:
            ex, _, _ = run_signal(panel, tr, members_by_date, nm, top_frac, cost_bps)
            if len(ex) and float(np.mean(ex)) > best_v:
                best, best_v = nm, float(np.mean(ex))
        if best is None:
            continue
        ex_te, _, _ = run_signal(panel, te, members_by_date, best, top_frac, cost_bps)
        if len(ex_te):
            picks.append((best, float(np.mean(ex_te))))
    vals = [v for _, v in picks]
    return picks, (float(np.mean(vals)) if vals else 0.0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-dir", required=True)
    p.add_argument("--timeline", default="docs/universe_timeline.json")
    p.add_argument("--candidates", default="docs/universe_candidates.json")
    p.add_argument("--top-frac", type=float, default=0.10)
    p.add_argument("--cost-bps", type=float, default=14.0)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--null-draws", type=int, default=40)
    p.add_argument(
        "--include-flagged",
        action="store_true",
        help="Ignore the screening flags (research only)",
    )
    args = p.parse_args(argv)

    cands = json.loads(Path(args.candidates).read_text())
    if args.include_flagged:
        allowed = {c["base"] for c in cands}
        label = "all candidates (flags ignored)"
    else:
        allowed = {c["base"] for c in cands if c["known"] and not c["flags"]}
        label = "unflagged only"

    tl = json.loads(Path(args.timeline).read_text())
    members_by_date = {}
    for ds, names in tl.items():
        t = int(
            datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            * 1000
        )
        keep = [n for n in names if n in allowed]
        if keep:
            members_by_date[t] = keep
    dates = sorted(members_by_date)
    universe_syms = sorted({n for v in members_by_date.values() for n in v})
    panel = load_panel(Path(args.cache_dir), universe_syms)

    step_days = (dates[1] - dates[0]) / DAY_MS if len(dates) > 1 else 30
    per_year = 365.25 / max(1.0, step_days)
    print(
        f"universe: {label} | {len(universe_syms)} names ever | {len(panel)} with data"
    )
    print(
        f"{len(dates)} rebalance dates, {step_days:.0f}-day step "
        f"({per_year:.1f}/yr) | top {args.top_frac:.0%} | cost {args.cost_bps:g}bps"
    )
    sizes = [len(members_by_date[d]) for d in dates]
    print(
        f"universe size: first {sizes[0]}, median {int(np.median(sizes))}, "
        f"last {sizes[-1]}\n"
    )

    rng = np.random.default_rng(11)
    thr = random_null(
        panel,
        dates,
        members_by_date,
        args.top_frac,
        args.cost_bps,
        len(SIGNALS),
        args.null_draws,
        rng,
    )
    print(
        f"null threshold (best of {len(SIGNALS)} under no edge, 95th pct): "
        f"{thr * 100:+.3f}% per period\n"
    )

    hdr = f"{'signal':20} {'periods':>8} {'excess/pd':>11} {'ann.':>9} {'Sharpe':>8}  verdict"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for nm in SIGNALS:
        ex, turn, bench = run_signal(
            panel, dates, members_by_date, nm, args.top_frac, args.cost_bps
        )
        if not len(ex):
            print(f"{nm:20} {'-':>8}  insufficient data")
            continue
        m = float(np.mean(ex))
        sh = sharpe(ex, per_year)
        ann = m * per_year
        v = "clears null" if m > thr else "within noise"
        rows.append((nm, m, sh, ann))
        print(
            f"{nm:20} {len(ex):8} {m * 100:+10.3f}% {ann * 100:+8.2f}% {sh:8.2f}  {v}"
        )

    print(
        f"\nbenchmark (equal-weight universe): "
        f"{float(np.mean(bench)) * 100:+.3f}% per period\n"
    )

    picks, wf = walk_forward(
        panel, dates, members_by_date, args.top_frac, args.cost_bps, args.folds
    )
    print("walk-forward -- pick the best signal on each fold, trade it on the next:")
    for nm, v in picks:
        print(f"    picked {nm:20} -> {v * 100:+.3f}% per period")
    pos = sum(1 for _, v in picks if v > 0)
    print(f"  mean {wf * 100:+.3f}% per period, positive in {pos}/{len(picks)} folds")

    best = max(rows, key=lambda r: r[1]) if rows else None
    print("\n" + "=" * 72)
    print("PRE-REGISTERED CRITERIA")
    print("=" * 72)
    if best is None:
        print("  no signal produced enough periods to judge")
        return 1
    nm, m, sh, ann = best
    c1 = m > 0
    c2 = m > thr
    c3 = sh >= 0.8
    c4 = len(picks) > 0 and pos > len(picks) / 2
    for ok, text in (
        (c1, f"1. excess > 0                    best={nm} {m * 100:+.3f}%/pd"),
        (c2, f"2. clears best-of-N null         null={thr * 100:+.3f}%/pd"),
        (c3, f"3. annualised Sharpe >= 0.8      Sharpe={sh:.2f}"),
        (c4, f"4. positive in majority of folds {pos}/{len(picks)}"),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
    ok = c1 and c2 and c3 and c4
    print(
        "\n"
        + (
            "RESULT: criteria met -- worth a closer look"
            if ok
            else "RESULT: negative. At least one criterion failed, "
            "which the pre-registration\n        defines as a "
            "negative result. No re-running with adjusted\n"
            "        parameters."
        )
    )
    print(
        "\nNote: a name that stopped trading is held to its last print, so its "
        "loss\nis taken. That is still mildly optimistic -- it assumes you sold "
        "at that\nprint rather than being stuck in a halted market."
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
