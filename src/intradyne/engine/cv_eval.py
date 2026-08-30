from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .eval import evaluate


def split_windows(
    start: pd.Timestamp, end: pd.Timestamp, stride_days: int
) -> List[tuple[int, int]]:
    windows: List[tuple[int, int]] = []
    cur = start
    while cur < end:
        nxt = min(cur + pd.Timedelta(days=stride_days), end)
        windows.append((int(cur.timestamp() * 1000), int(nxt.timestamp() * 1000)))
        cur = nxt
    return windows


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True)
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument(
        "--strategy", type=str, choices=["momentum", "meanrev"], default="momentum"
    )
    p.add_argument("--params-file", type=str, required=True)
    p.add_argument("--stride-days", type=int, default=7)
    p.add_argument("--fees-maker-bps", type=int, default=2)
    p.add_argument("--fees-taker-bps", type=int, default=10)
    p.add_argument("--slippage-bps", type=int, default=8)
    ns = p.parse_args(argv)

    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start = pd.Timestamp(ns.start, tz="UTC")
    end = pd.Timestamp(ns.end, tz="UTC")
    windows = split_windows(start, end, ns.stride_days)

    details: List[Dict[str, Any]] = []
    for s_ms, e_ms in windows:
        # Evaluate per window; evaluate() writes artifacts/report.json each time — we aggregate here too
        out = evaluate(
            symbols,
            [(s_ms, e_ms)],
            ns.timeframe,
            Path(ns.params_file),
            strategy=ns.strategy,
            maker_bps=ns.fees_maker_bps,
            taker_bps=ns.fees_taker_bps,
            slippage_bps=ns.slippage_bps,
        )
        data = json.loads(Path(out).read_text())
        if data.get("details"):
            details.extend(data["details"])

    # Aggregate
    n = len(details)
    avg_sharpe = sum(d.get("sharpe", 0.0) for d in details) / max(1, n)
    avg_pnl = sum(d.get("net_pnl", 0.0) for d in details) / max(1, n)
    avg_dd = sum(d.get("max_dd", 0.0) for d in details) / max(1, n)
    report = {
        "windows": n,
        "avg_sharpe": avg_sharpe,
        "avg_net_pnl": avg_pnl,
        "avg_max_dd": avg_dd,
        "details": details,
        "fees_maker_bps": ns.fees_maker_bps,
        "fees_taker_bps": ns.fees_taker_bps,
        "slippage_bps": ns.slippage_bps,
    }
    path = Path("artifacts") / "report_cv.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(f"CV report saved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
