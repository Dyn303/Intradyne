from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.backtest import run as run_backtest


def map_params(strategy: str, best_params_path: Path) -> Dict[str, Any]:
    raw = json.loads(best_params_path.read_text())
    params: Dict[str, Any] = {strategy: {}, "risk": {}}
    if strategy == "momentum":
        if "m_breakout_window" in raw:
            params[strategy]["breakout_window"] = int(raw["m_breakout_window"])
        if "m_min_range_bps" in raw:
            params[strategy]["min_range_bps"] = int(raw["m_min_range_bps"])
    elif strategy == "meanrev":
        if "r_window" in raw:
            params[strategy]["window"] = int(raw["r_window"])
        if "r_band_width" in raw:
            params[strategy]["k"] = float(raw["r_band_width"])
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
    return params


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=["momentum", "meanrev"], required=True)
    ap.add_argument("--symbols", type=str, default="BTC/USDT,ETH/USDT")
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--timeframe", type=str, default="1m")
    ap.add_argument("--fees-maker-bps", type=int, default=2)
    ap.add_argument("--fees-taker-bps", type=int, default=10)
    ap.add_argument("--slippage-bps", type=int, default=8)
    ns = ap.parse_args()

    strat = ns.strategy
    best_path = Path("artifacts") / f"best_params_{strat}.json"
    if not best_path.exists():
        # fall back to generic
        best_path = Path("artifacts") / "best_params.json"
    params = map_params(strat, best_path)

    symbols: List[str] = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start_ms = int(pd.Timestamp(ns.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(ns.end, tz="UTC").timestamp() * 1000)

    res = run_backtest(
        symbols,
        start_ms,
        end_ms,
        ns.timeframe,
        strat,
        params,
        maker_bps=ns.fees_maker_bps,
        taker_bps=ns.fees_taker_bps,
        slippage_bps=ns.slippage_bps,
        seed=123,
    )
    print(json.dumps(res.metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
