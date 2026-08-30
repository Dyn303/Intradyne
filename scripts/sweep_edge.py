#!/usr/bin/env python
"""Sweep take-profit / stop-loss geometry and score each run by expectancy.

    python scripts/sweep_edge.py --symbols BTC/USDT --start 2024-01-01 \
        --end 2024-03-01 --timeframe 1m

Scores by expectancy after fees rather than by win rate or PnL. A high win
rate at a tight target loses money; a low win rate at a wide target can make
it. Only expectancy settles which.

Use --holdout to split the window and report in-sample and out-of-sample
separately: a sweep that reports only its best in-sample result is a way of
fooling yourself, since the best of N random configurations always looks good.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intradyne.backtester.costs import assess  # noqa: E402


def _ms(date_str: str) -> int:
    return int(
        datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        * 1000
    )


def _run_one(
    symbols: List[str],
    start_ms: int,
    end_ms: int,
    timeframe: str,
    strategy: str,
    tp_bps: float,
    sl_bps: float,
    fees: Dict[str, int],
    out_root: Path,
    time_stop_s: int = 120,
) -> Optional[Dict[str, Any]]:
    from intradyne.engine.backtest import run

    params = {
        "risk": {"tp_pct": tp_bps / 1e4, "per_trade_sl_pct": sl_bps / 1e4},
        # The exit horizon dominates everything else. At the shipped 120s
        # against 1m bars, ~95% of positions close on the time stop before
        # either target or stop is reached, which makes the whole take-profit
        # / stop-loss geometry inert -- every configuration then produces the
        # same result.
        "execution": {"time_stop_s": time_stop_s},
    }
    tag = f"tp{int(tp_bps)}_sl{int(sl_bps)}_t{time_stop_s}"
    try:
        metrics = run(
            symbols,
            start_ms,
            end_ms,
            timeframe,
            strategy,
            params,
            fees["maker"],
            fees["taker"],
            fees["slippage"],
            seed=42,
            out_dir=out_root / tag,
            fast_mode=True,
        ).metrics
    except Exception as exc:  # noqa: BLE001
        print(f"  {tag}: failed ({exc})")
        return None

    edge = assess(
        win_rate=float(metrics.get("win_rate") or 0.0),
        tp_pct=tp_bps / 1e4,
        sl_pct=sl_bps / 1e4,
        taker_bps=fees["taker"],
        slippage_bps=fees["slippage"],
        maker_bps=fees["maker"],
        trades=int(metrics.get("round_trips") or 0),
    )
    return {
        "tp_bps": tp_bps,
        "sl_bps": sl_bps,
        "round_trips": int(metrics.get("round_trips") or 0),
        "win_rate": float(metrics.get("win_rate") or 0.0),
        "net_pnl": float(metrics.get("net_pnl") or 0.0),
        "max_dd": float(metrics.get("max_dd") or 0.0),
        "breakeven": edge.breakeven_win_rate,
        "expectancy_bps": edge.expectancy_pct * 1e4,
        "verdict": edge.verdict,
    }


def _table(rows: List[Dict[str, Any]], title: str) -> None:
    print(f"\n{title}")
    header = (
        f"{'tp':>5} {'sl':>5} {'trips':>7} {'win':>7} {'b/e':>7} "
        f"{'exp bps':>9} {'net pnl':>10}  verdict"
    )
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda x: -x["expectancy_bps"]):
        be = f"{r['breakeven']:7.0%}" if r["breakeven"] is not None else "      -"
        print(
            f"{r['tp_bps']:5.0f} {r['sl_bps']:5.0f} {r['round_trips']:7d} "
            f"{r['win_rate']:7.1%} {be} {r['expectancy_bps']:9.2f} "
            f"{r['net_pnl']:10.2f}  {r['verdict']}"
        )


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--strategy", default="momentum", choices=["momentum", "meanrev"])
    p.add_argument("--tp-bps", default="20,30,40,60,100")
    p.add_argument("--sl-bps", default="20,30,40,60")
    p.add_argument("--maker-bps", type=int, default=2)
    p.add_argument("--taker-bps", type=int, default=5)
    p.add_argument("--slippage-bps", type=int, default=2)
    p.add_argument(
        "--time-stop-s",
        dest="time_stop_s",
        type=int,
        default=120,
        help="Max holding period. At the 120s default on 1m bars nearly every "
        "position closes on time, making tp/sl inert.",
    )
    p.add_argument("--out", default="artifacts/sweeps")
    p.add_argument(
        "--holdout",
        type=float,
        default=0.0,
        help="Fraction of the window held out; reports in- and out-of-sample",
    )
    args = p.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    tps = [float(x) for x in args.tp_bps.split(",")]
    sls = [float(x) for x in args.sl_bps.split(",")]
    fees = {
        "maker": args.maker_bps,
        "taker": args.taker_bps,
        "slippage": args.slippage_bps,
    }
    start, end = _ms(args.start), _ms(args.end)
    out_root = Path(args.out)

    split = end
    if args.holdout > 0:
        split = start + int((end - start) * (1.0 - args.holdout))

    combos = list(itertools.product(tps, sls))
    print(f"{len(combos)} configurations, {args.strategy} on {symbols}")

    in_sample: List[Dict[str, Any]] = []
    for tp, sl in combos:
        row = _run_one(
            symbols,
            start,
            split,
            args.timeframe,
            args.strategy,
            tp,
            sl,
            fees,
            out_root / "in_sample",
            args.time_stop_s,
        )
        if row:
            in_sample.append(row)
    if not in_sample:
        print("no runs produced results")
        return 1
    _table(in_sample, "In sample" if args.holdout else "Results")

    if args.holdout <= 0:
        print(
            "\nNo holdout requested. The best row here is the best of "
            f"{len(combos)} tries on one window; re-run with --holdout 0.3 "
            "before believing it."
        )
        return 0

    best = max(in_sample, key=lambda r: r["expectancy_bps"])
    print(
        f"\nBest in sample: tp={best['tp_bps']:.0f} sl={best['sl_bps']:.0f} "
        f"({best['expectancy_bps']:+.2f} bps). Re-running it out of sample."
    )
    oos = _run_one(
        symbols,
        split,
        end,
        args.timeframe,
        args.strategy,
        best["tp_bps"],
        best["sl_bps"],
        fees,
        out_root / "out_of_sample",
        args.time_stop_s,
    )
    if oos:
        _table([oos], "Out of sample (the number that counts)")
        if oos["expectancy_bps"] <= 0:
            print(
                "\nThe in-sample winner does not hold up out of sample. That is "
                "the usual outcome and means the configuration was fitted to "
                "noise, not that the sweep failed."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
