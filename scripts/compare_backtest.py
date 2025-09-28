from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import sys


def _ensure_src_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def run_compare(days: int, symbols: List[str]) -> Dict[str, Any]:
    _ensure_src_on_path()
    from backtester.engine import run_backtest

    # Baseline: simple MA(20) crossover
    base = run_backtest(
        days=days,
        symbols=[s if "/" in s else f"{s}/USDT" for s in symbols],
        ma_window=20,
        report=True,
    )
    # Tuned: bar-confirmation, higher trend EMA, larger TP multiple,
    # ATR-based sizing throttle, and basic regime classification.
    tuned = run_backtest(
        days=days,
        symbols=[s if "/" in s else f"{s}/USDT" for s in symbols],
        ma_window=20,
        trend_ema=100,
        atr_window=14,
        sl_atr_k=1.5,
        tp_atr_k=2.5,
        risk_per_trade=0.008,  # ~0.8% target per trade
        confirm_bars=2,
        atr_entry_min=0.001,  # skip very choppy (<10bps avg move)
        atr_entry_max=0.02,  # skip too volatile (>2% avg move)
        regime=True,
        use_sentiment=True,
        sentiment_min=0.0,
        size_min=0.9,
        size_max=1.2,
        report=True,
    )
    return {"baseline": base, "tuned": tuned}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbols", type=str, default="BTC,ETH")
    ns = ap.parse_args()
    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    out = run_compare(ns.days, symbols)
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "artifacts" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"compare_{int(time.time())}.json"
    fp.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
