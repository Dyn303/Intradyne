from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import load_settings
from .backtest import run as run_backtest


def evaluate(symbols: List[str], windows: List[tuple[int, int]], timeframe: str, params_file: Path, strategy: str = "momentum", maker_bps: int = 2, taker_bps: int = 5, slippage_bps: int = 2) -> Path:
    settings = load_settings()
    artifacts = Path(settings.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    raw = json.loads(params_file.read_text())
    # Accept either nested param dict or flat Optuna params; map when flat
    if any(k.startswith("m_") or k.startswith("r_") or k.startswith("risk_") for k in raw.keys()):
        params: Dict[str, Any] = {
            "momentum": {},
            "meanrev": {},
            "risk": {},
        }
        if "m_breakout_window" in raw:
            params["momentum"]["breakout_window"] = int(raw["m_breakout_window"])
        if "m_min_range_bps" in raw:
            params["momentum"]["min_range_bps"] = int(raw["m_min_range_bps"])
        if "r_window" in raw:
            params["meanrev"]["window"] = int(raw["r_window"])
        if "r_band_width" in raw:
            params["meanrev"]["k"] = float(raw["r_band_width"])
        # Risk
        for rk, nk in (
            ("risk_max_pos_pct", "max_pos_pct"),
            ("risk_dd_soft", "dd_soft"),
            ("risk_dd_hard", "dd_hard"),
            ("risk_sl_pct", "per_trade_sl_pct"),
            ("risk_tp_pct", "tp_pct"),
        ):
            if rk in raw:
                params["risk"][nk] = float(raw[rk])
    else:
        params = raw

    results: List[Dict[str, Any]] = []
    for (start_ms, end_ms) in windows:
        res = run_backtest(symbols, start_ms, end_ms, timeframe, strategy, params, maker_bps=maker_bps, taker_bps=taker_bps, slippage_bps=slippage_bps, seed=123)
        results.append(res.metrics)

    # Aggregate
    agg = {
        "windows": len(results),
        "avg_sharpe": sum(r.get("sharpe", 0.0) for r in results) / max(1, len(results)),
        "avg_net_pnl": sum(r.get("net_pnl", 0.0) for r in results) / max(1, len(results)),
        "avg_max_dd": sum(r.get("max_dd", 0.0) for r in results) / max(1, len(results)),
        "details": results,
    }
    out = artifacts / "report.json"
    out.write_text(json.dumps(agg, indent=2))
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True)
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument("--params-file", type=str, required=True)
    p.add_argument("--strategy", type=str, choices=["momentum", "meanrev"], default="momentum")
    p.add_argument("--fees-maker-bps", type=int, default=2)
    p.add_argument("--fees-taker-bps", type=int, default=5)
    p.add_argument("--slippage-bps", type=int, default=2)
    return p.parse_args()


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args()
    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start = pd.Timestamp(ns.start, tz="UTC")
    end = pd.Timestamp(ns.end, tz="UTC")
    # Single window; could split into multiple disjoint ones for CV
    windows = [(int(start.timestamp() * 1000), int(end.timestamp() * 1000))]
    evaluate(symbols, windows, ns.timeframe, Path(ns.params_file), strategy=ns.strategy, maker_bps=ns.fees_maker_bps, taker_bps=ns.fees_taker_bps, slippage_bps=ns.slippage_bps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
