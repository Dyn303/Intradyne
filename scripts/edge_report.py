#!/usr/bin/env python
"""Report whether backtest runs clear their breakeven win rate.

Two modes:

    # What win rate would this configuration need?
    python scripts/edge_report.py --breakeven

    # How did existing runs actually do?
    python scripts/edge_report.py --runs artifacts/backtests

A win rate on its own is not evidence for a scalper: at a 20bps take-profit
against a 30bps stop, 14bps of round-trip cost means ~88% of trades must win
merely to break even, so a 70% win rate looks strong and loses money.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intradyne.backtester.costs import assess, breakeven_win_rate  # noqa: E402
from intradyne.backtester.costs import round_trip_cost_pct  # noqa: E402


def _load_runs(root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for summary in sorted(root.rglob("summary.json")):
        try:
            body = json.loads(summary.read_text(encoding="utf-8"))
        except Exception:
            continue
        body["_run"] = summary.parent.name
        out.append(body)
    return out


def _print_breakeven(args: argparse.Namespace) -> None:
    cost = round_trip_cost_pct(args.taker_bps, args.slippage_bps)
    maker_cost = round_trip_cost_pct(
        args.taker_bps,
        args.slippage_bps,
        maker_bps=args.maker_bps,
        maker_entry=True,
        maker_exit=True,
    )
    print("Configuration")
    print(f"  take profit        {args.tp_pct * 1e4:8.1f} bps")
    print(f"  stop loss          {args.sl_pct * 1e4:8.1f} bps")
    print(
        f"  taker + slippage   {args.taker_bps + args.slippage_bps:8.1f} bps per side"
    )
    print()
    for label, c in (("all taker fills", cost), ("all maker fills", maker_cost)):
        be = breakeven_win_rate(args.tp_pct, args.sl_pct, c)
        print(f"{label}")
        print(f"  round-trip cost    {c * 1e4:8.1f} bps")
        print(f"  net win            {(args.tp_pct - c) * 1e4:8.1f} bps")
        print(f"  net loss           {(args.sl_pct + c) * 1e4:8.1f} bps")
        if be is None:
            print("  breakeven win rate      impossible (cost exceeds take profit)")
        else:
            print(f"  breakeven win rate {be:8.1%}")
        print()


def _print_runs(args: argparse.Namespace) -> int:
    root = Path(args.runs)
    if not root.exists():
        print(f"no such directory: {root}")
        return 1
    runs = _load_runs(root)
    if not runs:
        print(f"no summary.json found under {root}")
        return 1

    print(f"{len(runs)} run(s) under {root}\n")
    header = f"{'run':34} {'trades':>7} {'win':>7} {'b/e':>7} {'margin':>8}  verdict"
    print(header)
    print("-" * len(header))

    counts: Dict[str, int] = {}
    for body in runs:
        trades = int(body.get("trades") or 0)
        win = float(body.get("win_rate") or 0.0)
        result = assess(
            win_rate=win,
            tp_pct=args.tp_pct,
            sl_pct=args.sl_pct,
            taker_bps=args.taker_bps,
            slippage_bps=args.slippage_bps,
            maker_bps=args.maker_bps,
            trades=trades,
        )
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
        be = (
            f"{result.breakeven_win_rate:7.1%}"
            if result.breakeven_win_rate is not None
            else "      -"
        )
        margin = f"{result.margin:8.1%}" if result.margin is not None else "       -"
        print(
            f"{body['_run'][:34]:34} {trades:7d} {win:7.1%} {be} {margin}  "
            f"{result.verdict}"
        )

    print("\nverdicts")
    for verdict, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:20} {n}")

    passing = counts.get("clears_with_margin", 0)
    print()
    if passing:
        print(f"{passing} run(s) clear breakeven with margin.")
    else:
        print(
            "No run clears breakeven with margin. These are not evidence of an "
            "edge, whatever their PnL says."
        )
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=str, help="Directory of backtest run folders")
    p.add_argument(
        "--breakeven",
        action="store_true",
        help="Print the breakeven win rate for the configuration and exit",
    )
    p.add_argument("--tp-pct", dest="tp_pct", type=float, default=0.002)
    p.add_argument("--sl-pct", dest="sl_pct", type=float, default=0.003)
    p.add_argument("--taker-bps", dest="taker_bps", type=float, default=5.0)
    p.add_argument("--maker-bps", dest="maker_bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", dest="slippage_bps", type=float, default=2.0)
    args = p.parse_args(argv)

    if args.breakeven or not args.runs:
        _print_breakeven(args)
        if not args.runs:
            return 0
    return _print_runs(args)


if __name__ == "__main__":
    raise SystemExit(main())
