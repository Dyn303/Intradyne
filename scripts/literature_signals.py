#!/usr/bin/env python
"""Test two pre-specified signals from the literature.

    python scripts/literature_signals.py --data DIR --symbols BTCUSDT,ETHUSDT,...

Every search in this project has died at the same place: a strategy clears its
costs, then fails to beat the best-of-N null for its own trade count. That
penalty is the price of *searching*. A signal specified in advance by someone
else does not pay it -- there is no selection, so the t-statistic means what it
says.

Two signals, taken as published rather than tuned:

**1. Intraday momentum (Gao, Han, Li & Zhou, JFE 2018).** The first half-hour
return of the day predicts the last half-hour return. In SPY they report a
scaled slope of 6.94, significant at 1%, R^2 1.6%, stronger on high-volatility
and high-volume days. Shen et al. (Financial Review 2022) report it on Bitcoin
with break-even costs above typical crypto levels.

A prior worth stating before the result: in equities the effect is attributed
to opening auctions and late-day portfolio rebalancing. Crypto has neither --
it trades continuously with no auction and no close. The UTC day boundary used
here is a convention, not a market mechanism, so the causal story does not
obviously transfer even if the correlation does.

**2. Short-horizon cross-sectional momentum.** The crypto momentum literature
places the effect at 1-4 week formation with persistence limited to about a
week, unlike the 12-month effect in equities. The cross-sectional test run
earlier in this project used 1, 3, 6 and 12 month formation -- mostly outside
that window. This tests the window the literature actually points at.

Both are long-only and spot, matching the live rule system.

Scoring is deliberately plain: the mean, its t-statistic, and the return net of
costs. Because two hypotheses are tested here (five including the formation
variants), and because the field's multiple-testing work puts the credible
hurdle at t ~ 3.4-3.8 rather than 1.96, the verdict line uses 3.0 as a floor
rather than the conventional 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_klines_archive import FIELDS, months  # noqa: E402

DAY_MS = 86_400_000
COST_MAKER_BPS = 4.0
COST_TAKER_BPS = 14.0
#: The field's multiple-testing hurdle, not the textbook 1.96.
CREDIBLE_T = 3.0


def load_intraday(cache: Path, symbol: str, tf: str, start: str, end: str):
    parts = []
    for m in months(start, end):
        f = cache / f"{symbol}-{tf}-{m}.npz"
        if f.exists():
            z = np.load(f)
            parts.append({k: z[k] for k in FIELDS})
    if not parts:
        return None
    return {k: np.concatenate([p[k] for p in parts]) for k in FIELDS}


# ---- signal 1: intraday momentum ---------------------------------------


def intraday_momentum(
    d, bar_minutes: int, window_min: int = 30
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (first-window return, last-window return) per UTC day.

    Taken exactly as specified: the first window predicts the last window, no
    parameter fitting. The day is UTC midnight to midnight, which is a
    convention rather than a market event -- crypto has no open or close.
    """
    ts, close, open_ = d["ts"], d["close"], d["open"]
    k = max(1, window_min // bar_minutes)
    day = (ts // 86400).astype("int64")
    firsts, lasts = [], []
    for dd in np.unique(day):
        m = np.where(day == dd)[0]
        # A partial day cannot supply both windows without overlap.
        if len(m) < 4 * k:
            continue
        a, b = m[:k], m[-k:]
        r_first = close[a[-1]] / open_[a[0]] - 1.0
        r_last = close[b[-1]] / open_[b[0]] - 1.0
        if np.isfinite(r_first) and np.isfinite(r_last):
            firsts.append(r_first)
            lasts.append(r_last)
    return np.array(firsts), np.array(lasts)


def test_intraday_momentum(
    panels: Dict[str, dict], bar_minutes: int
) -> Dict[str, object]:
    F, L, per_sym = [], [], {}
    for sym, d in panels.items():
        f, lastw = intraday_momentum(d, bar_minutes)
        if f.size < 100:
            continue
        F.append(f)
        L.append(lastw)
        # Long-only: take the last window when the first window was positive.
        taken = lastw[f > 0]
        per_sym[sym] = (
            int(taken.size),
            float(taken.mean() * 1e4) if taken.size else 0.0,
        )
    if not F:
        return {"days": 0}
    f, lastw = np.concatenate(F), np.concatenate(L)

    # The published test: does the first window predict the last?
    slope = float(np.polyfit(f, lastw, 1)[0])
    r = float(np.corrcoef(f, lastw)[0, 1])

    long_days, skipped = lastw[f > 0], lastw[f <= 0]
    mean_bps = float(long_days.mean() * 1e4)
    uncond = float(lastw.mean() * 1e4)

    # The t-statistic has to be on the *signal*, not on the raw return.
    # These instruments drift upward, so the mean of any long position is
    # positive and its t-statistic large whether or not the rule discriminates.
    # What the rule claims is that days following a positive first window beat
    # days following a negative one, so that difference is what gets tested.
    diff_bps = mean_bps - float(skipped.mean() * 1e4)
    se_diff = float(
        np.sqrt(
            long_days.var(ddof=1) / long_days.size
            + skipped.var(ddof=1) / max(1, skipped.size)
        )
        * 1e4
    )
    return {
        "days": int(f.size),
        "trades": int(long_days.size),
        "slope": slope,
        "r": r,
        "r2": r * r,
        "mean_bps": mean_bps,
        "t_raw": mean_bps / (long_days.std(ddof=1) / np.sqrt(long_days.size) * 1e4),
        "skipped_bps": float(skipped.mean() * 1e4),
        "diff_bps": diff_bps,
        "t": diff_bps / se_diff if se_diff > 0 else 0.0,
        "uncond_bps": uncond,
        "excess_bps": mean_bps - uncond,
        "net_maker": mean_bps - COST_MAKER_BPS,
        "net_taker": mean_bps - COST_TAKER_BPS,
        "per_symbol": per_sym,
    }


# ---- signal 2: short-horizon cross-sectional momentum -------------------


def load_daily(cache_dir: Path, symbols: List[str]) -> Dict[str, dict]:
    panel = {}
    for s in symbols:
        # The timeline stores bare tickers; the daily cache is keyed by pair.
        f = cache_dir / f"{s}USDT.npz"
        if f.exists():
            z = np.load(f)
            panel[s] = {"ts": z["ts"], "close": z["close"], "qvol": z["qvol"]}
    return panel


def _px_at(d, t_ms: int) -> Optional[float]:
    i = np.searchsorted(d["ts"], t_ms, side="right") - 1
    return float(d["close"][i]) if i >= 0 else None


def cross_sectional_weekly(
    panel,
    members_by_date,
    formation_days: int,
    hold_days: int,
    top_frac: float,
    cost_bps: float,
) -> Dict[str, float]:
    """Rank on trailing return, hold the top slice, rebalance weekly."""
    dates = sorted(members_by_date)
    step = hold_days * DAY_MS
    grid = list(range(dates[0], dates[-1], step))
    excess, held_prev = [], set()
    for t0, t1 in zip(grid, grid[1:]):
        # Membership from the nearest earlier snapshot: never look forward.
        snap = max((d for d in dates if d <= t0), default=None)
        if snap is None:
            continue
        members = members_by_date[snap]
        scored, rets = [], {}
        for s in members:
            d = panel.get(s)
            if d is None:
                continue
            p_now, p_then = _px_at(d, t0), _px_at(d, t0 - formation_days * DAY_MS)
            p_next = _px_at(d, t1)
            if not p_now or not p_then or not p_next or p_then <= 0:
                continue
            scored.append((p_now / p_then - 1.0, s))
            rets[s] = p_next / p_now - 1.0
        if len(scored) < 10:
            continue
        scored.sort(reverse=True)
        k = max(3, int(len(scored) * top_frac))
        picks = {s for _, s in scored[:k]}
        held_rets = [rets[s] for s in picks if s in rets]
        if not held_rets:
            continue
        turn = len(picks - held_prev) / max(1, len(picks))
        net = float(np.mean(held_rets)) - turn * (cost_bps / 1e4)
        excess.append(net - float(np.mean(list(rets.values()))))
        held_prev = picks
    if not excess:
        return {"periods": 0}
    e = np.array(excess)
    se = e.std(ddof=1) / np.sqrt(e.size)
    per_year = 365.25 / hold_days
    return {
        "periods": int(e.size),
        "excess_pct": float(e.mean() * 100),
        "t": float(e.mean() / se) if se > 0 else 0.0,
        "sharpe": float(e.mean() / e.std(ddof=1) * np.sqrt(per_year))
        if e.std(ddof=1) > 0
        else 0.0,
        "positive_frac": float((e > 0).mean()),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--symbols", required=True)
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--bar-minutes", type=int, default=5)
    p.add_argument("--from", dest="start", default="2024-01")
    p.add_argument("--to", dest="end", default="2026-07")
    p.add_argument(
        "--daily-cache",
        default=None,
        help="Directory of daily npz for the cross-sectional test",
    )
    p.add_argument("--timeline", default="docs/universe_timeline.json")
    args = p.parse_args(argv)

    cache = Path(args.data) / "bars"
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    panels = {}
    for s in syms:
        d = load_intraday(cache, s, args.timeframe, args.start, args.end)
        if d is not None:
            panels[s] = d
    print(
        f"signal 1: intraday momentum -- {len(panels)} instruments, "
        f"{args.timeframe} bars\n"
    )

    r1 = test_intraday_momentum(panels, args.bar_minutes)
    if r1.get("days"):
        print("  first-30min return predicts last-30min return")
        print(f"    day-observations      : {r1['days']:,}")
        print(
            f"    slope                 : {r1['slope']:+.3f}  "
            f"(Gao et al. report +6.94 scaled, on SPY)"
        )
        print(f"    R^2                   : {r1['r2']:.5f}  (they report 0.016)")
        print()
        print(
            "  long-only timing rule: hold the last 30 min when the "
            "first 30 min was positive"
        )
        print(f"    trades                : {r1['trades']:,}")
        print(
            f"    mean                  : {r1['mean_bps']:+.2f} bps  t = {r1['t']:+.2f}"
        )
        print(f"    unconditional         : {r1['uncond_bps']:+.2f} bps")
        print(f"    excess over always-in : {r1['excess_bps']:+.2f} bps")
        print(f"    net of 4bps maker     : {r1['net_maker']:+.2f} bps")
        print(f"    net of 14bps taker    : {r1['net_taker']:+.2f} bps")
        ok = r1["t"] > CREDIBLE_T and r1["net_maker"] > 0
        print(
            f"    verdict               : "
            f"{'CREDIBLE' if ok else 'not supported'} "
            f"(needs t > {CREDIBLE_T:g} and positive net)"
        )
        by = sorted(r1["per_symbol"].items(), key=lambda kv: -kv[1][1])
        pos = sum(1 for _, (_, m) in by if m > 0)
        print(f"    positive on {pos}/{len(by)} instruments")
    else:
        print("  insufficient data")

    print("\n" + "=" * 72)
    print("signal 2: cross-sectional momentum at the literature's window")
    print("=" * 72)
    if not args.daily_cache:
        print("  skipped (--daily-cache not given)")
        return 0
    tl = json.loads(Path(args.timeline).read_text())
    mbd = {}
    for ds, names in tl.items():
        t = int(
            datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            * 1000
        )
        mbd[t] = names
    universe = sorted({n for v in mbd.values() for n in v})
    panel = load_daily(Path(args.daily_cache), universe)
    print(
        f"  {len(panel)} instruments with daily data, {len(mbd)} universe snapshots\n"
    )
    print(
        f"  {'formation':>10} {'periods':>8} {'excess/wk':>11} {'t':>7} "
        f"{'Sharpe':>8} {'pos':>6}  verdict"
    )
    print("  " + "-" * 66)
    out = {}
    for weeks in (1, 2, 3, 4):
        r = cross_sectional_weekly(
            panel,
            mbd,
            formation_days=weeks * 7,
            hold_days=7,
            top_frac=0.10,
            cost_bps=COST_TAKER_BPS,
        )
        if not r.get("periods"):
            continue
        ok = r["t"] > CREDIBLE_T and r["excess_pct"] > 0
        out[f"{weeks}w"] = r
        print(
            f"  {weeks:>9}w {r['periods']:>8} {r['excess_pct']:>+10.3f}% "
            f"{r['t']:>+7.2f} {r['sharpe']:>8.2f} {r['positive_frac']:>5.0%}  "
            f"{'CREDIBLE' if ok else 'not supported'}"
        )

    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/literature_signals.json").write_text(
        json.dumps(
            {
                "intraday_momentum": {k: v for k, v in r1.items() if k != "per_symbol"},
                "cross_sectional": out,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
