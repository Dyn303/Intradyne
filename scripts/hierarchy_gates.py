#!/usr/bin/env python
"""Test a conditional trading hierarchy as gates rather than a weighted score.

    python scripts/hierarchy_gates.py --data DIR --symbols DOGEUSDT,ZECUSDT,...

The design under test:

    1 direction      where is the market going          market structure
    2 location       where should I look for an entry   VWAP + volume profile
    3 event          did something happen there         liquidity sweep
    4 participation  are real traders stepping in       volume / trade intensity
    5 pressure       is aggressive flow behind it       OFI / CVD / delta
    6 worth it       is the trade worth taking          volatility vs cost

**Gates, not weights.** A weighted score needs seven weights, and seven tuned
parameters is where overfitting lives -- this project already produced a
strategy that reached a day-clustered t of 4.58, beat its own null, was
positive on 10 of 10 instruments, and then lost money over the following
eleven months. Expressed as AND-gates the hierarchy has **no free parameters
to fit**, so whatever it shows is not a tuning artifact.

The weighting also implied an independence the data does not support. Measured
on ETH 5m over 20 months, market structure correlates with VWAP deviation at
0.72, with CVD slope at 0.56, and CVD with OFI at 0.45. Those five components
are one directional factor observed at different granularities; only
volatility and volume are genuinely independent of it. Seven components carry
about 3.2 independent dimensions, so a scheme placing 85% of its weight on the
directional cluster would feel far more confirmed than it is.

Gates are applied **cumulatively**, and each level is reported separately. The
useful question is not whether the full stack works, but whether each layer
earns its place: a gate that removes trades without raising edge per trade is
costing sample for nothing.

Thresholds are fixed at plain values stated here and are not searched. Windows
are reused from the existing predicate library rather than chosen fresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from multi_common import COST_MAKER_BPS, COST_TAKER_BPS, load_symbol  # noqa: E402
from strategy_search import (  # noqa: E402
    _ema,
    _roll_mean,
    _roll_min,
    _roll_std,
    forward_outcomes,
)

#: Fixed, unsearched. Stated so a reader can see nothing was fitted.
TREND_FAST, TREND_SLOW = 12, 72  # 1h / 6h on 5m bars
VWAP_WINDOW = 288  # one day
SWEEP_WINDOW = 60  # 5h low to sweep
FLOW_WINDOW = 12  # 1h of order flow
INTENSITY_BASE = 288
ATR_WINDOW = 60
#: Gate 6: the expected move must beat the round trip by this much.
COST_MULTIPLE = 3.0


def gates(b) -> Tuple[List[Tuple[str, np.ndarray]], np.ndarray]:
    """The six gates, plus the ATR used for exit sizing."""
    n = len(b)
    ret = np.diff(b.close, prepend=b.close[0]) / b.close
    atr = _roll_std(ret, ATR_WINDOW)

    # 1 direction: structure is up -- fast above slow, and price above both.
    ema_f, ema_s = _ema(b.close, TREND_FAST), _ema(b.close, TREND_SLOW)
    g_dir = (ema_f > ema_s) & (b.close > ema_s)

    # 2 location: price is at or below intraday fair value, not extended.
    pv, vv = (
        _roll_mean(b.close * b.volume, VWAP_WINDOW),
        _roll_mean(b.volume, VWAP_WINDOW),
    )
    vwap = np.where(vv > 0, pv / np.maximum(vv, 1e-12), b.close)
    g_loc = b.close <= vwap

    # 3 event: a liquidity sweep -- price took out a recent low and reclaimed it.
    prior_low = _roll_min(b.low, SWEEP_WINDOW)
    swept = np.zeros(n, dtype=bool)
    swept[1:] = (b.low[1:] <= np.nan_to_num(prior_low[:-1], nan=np.inf)) & (
        b.close[1:] > np.nan_to_num(prior_low[:-1], nan=np.inf)
    )
    g_evt = swept

    # 4 participation: more trades than usual -- someone is actually there.
    g_part = _roll_mean(b.trades, FLOW_WINDOW) > _roll_mean(b.trades, INTENSITY_BASE)

    # 5 pressure: aggressive buying dominates. This is the taker-buy split,
    #    which is what CVD, delta and order-flow imbalance all summarise.
    buy, sell = (
        _roll_mean(b.buy_volume, FLOW_WINDOW),
        _roll_mean(b.sell_volume, FLOW_WINDOW),
    )
    tot = buy + sell
    imb = np.where(tot > 0, (buy - sell) / np.maximum(tot, 1e-12), 0.0)
    g_pres = imb > 0.0

    # 6 worth it: the move available has to be worth the round trip.
    #    Expected move is taken as one ATR over the holding period.
    expected_move_bps = atr * 1e4 * np.sqrt(ATR_WINDOW)
    g_worth = expected_move_bps > COST_MULTIPLE * COST_MAKER_BPS

    return (
        [
            ("1 direction", np.nan_to_num(g_dir, nan=0).astype(bool)),
            ("2 location", np.nan_to_num(g_loc, nan=0).astype(bool)),
            ("3 sweep", np.nan_to_num(g_evt, nan=0).astype(bool)),
            ("4 participation", np.nan_to_num(g_part, nan=0).astype(bool)),
            ("5 pressure", np.nan_to_num(g_pres, nan=0).astype(bool)),
            ("6 worth it", np.nan_to_num(g_worth, nan=0).astype(bool)),
        ],
        atr,
    )


def trades_for(mask, gross, held) -> Tuple[np.ndarray, np.ndarray]:
    idx = np.where(mask & np.isfinite(gross))[0]
    picked, busy = [], -1
    for i in idx:
        if i > busy:
            picked.append(i)
            busy = i + max(int(held[i]), 1)
    if not picked:
        return np.array([]), np.array([])
    return gross[picked], np.asarray(picked)


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
    p.add_argument("--tp", type=float, default=150.0)
    p.add_argument("--sl", type=float, default=100.0)
    p.add_argument("--hold-min", type=int, default=240)
    args = p.parse_args(argv)

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    cache = Path(args.data) / "bars"
    hold_bars = max(1, args.hold_min // args.bar_minutes)

    def run(a: str, b: str) -> List[Dict[str, Any]]:
        levels: List[Dict[str, Any]] = []
        per_level_r: Dict[int, List[np.ndarray]] = {}
        per_level_sym: Dict[int, Dict[str, Tuple[int, float]]] = {}
        per_level_ts: Dict[int, List[np.ndarray]] = {}
        for sym in syms:
            panel = load_symbol(cache, sym, args.timeframe, a, b)
            if not panel:
                continue
            bars = panel["bars"]
            gl, _ = gates(bars)
            gross, held = forward_outcomes(bars, args.tp, args.sl, hold_bars, 0.0)
            cum = np.ones(len(bars), dtype=bool)
            for lvl, (_, g) in enumerate(gl):
                cum = cum & g
                r, idx = trades_for(cum, gross, held)
                if r.size:
                    per_level_r.setdefault(lvl, []).append(r)
                    per_level_ts.setdefault(lvl, []).append(bars.ts[idx])
                    per_level_sym.setdefault(lvl, {})[sym] = (
                        int(r.size),
                        float(r.mean()),
                    )
        for lvl, (name, _) in enumerate(
            gates(load_symbol(cache, syms[0], args.timeframe, a, b)["bars"])[0]
        ):
            rs = per_level_r.get(lvl, [])
            if not rs:
                levels.append({"gate": name, "trades": 0})
                continue
            pool = np.concatenate(rs)
            ts = np.concatenate(per_level_ts[lvl])
            day = (ts // 86400).astype("int64")
            o = np.argsort(day)
            daily = np.array(
                [
                    g.mean()
                    for g in np.split(pool[o], np.flatnonzero(np.diff(day[o])) + 1)
                ]
            )
            se = (
                float(daily.std(ddof=1) / np.sqrt(daily.size))
                if daily.size > 1
                else 0.0
            )
            by = per_level_sym[lvl]
            levels.append(
                {
                    "gate": name,
                    "trades": int(pool.size),
                    "gross_bps": float(pool.mean()),
                    "t_clustered": float(daily.mean() / se) if se > 0 else 0.0,
                    "win_rate": float((pool > 0).mean()),
                    "symbols": len(by),
                    "positive_syms": sum(1 for _, (_, m) in by.items() if m > 0),
                    "top_contributor": max(by, key=lambda k: by[k][0] * by[k][1])
                    if by
                    else None,
                    "top_share": (
                        lambda k: (
                            by[k][0] * by[k][1] / (pool.mean() * pool.size)
                            if pool.mean() * pool.size
                            else 0.0
                        )
                    )(max(by, key=lambda k: by[k][0] * by[k][1]))
                    if by
                    else 0.0,
                }
            )
        return levels

    print("hierarchy as cumulative AND-gates -- no weights, no fitted parameters")
    print(f"exit geometry fixed at tp{args.tp:g}/sl{args.sl:g}/{args.hold_min}m\n")
    for label, (a, b) in (
        ("TRAIN", (args.train, args.train_end)),
        ("TEST ", (args.test, args.test_end)),
    ):
        levels = run(a, b)
        print(f"=== {label} {a} to {b} ===")
        hdr = (
            f"  {'cumulative gate':18} {'trades':>8} {'gross':>8} {'t_cl':>6} "
            f"{'win':>7} {'net@4':>8} {'pos syms':>9} {'top name share':>15}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for lv in levels:
            if not lv["trades"]:
                print(f"  {lv['gate']:18} {'0':>8}  (no trades)")
                continue
            print(
                f"  {lv['gate']:18} {lv['trades']:>8,} {lv['gross_bps']:>+8.2f} "
                f"{lv['t_clustered']:>6.2f} {lv['win_rate']:>6.1%} "
                f"{lv['gross_bps'] - COST_MAKER_BPS:>+8.2f} "
                f"{lv['positive_syms']:>4}/{lv['symbols']:<4} "
                f"{lv['top_contributor'] or '':>9} {lv['top_share']:>5.0%}"
            )
        print()
        if label == "TRAIN":
            train_levels = levels
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/hierarchy_gates.json").write_text(
        json.dumps({"train": train_levels, "test": levels}, indent=1)
    )
    print("a gate earns its place only if it raises gross edge per trade.")
    print("one that removes trades without raising edge is costing sample for nothing.")
    print(f"cost floor: {COST_MAKER_BPS:g}bps all-maker, {COST_TAKER_BPS:g}bps taker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
