from __future__ import annotations

import argparse
import itertools as it
import json
import time
from pathlib import Path
from typing import Any, Dict, List
import sys


def run_bt(
    *,
    symbols: List[str],
    start: str,
    end: str,
    timeframe: str,
    ema_fast: int,
    ema_slow: int,
    atr_k_tp: float,
    atr_min: float,
    sent_min: float,
) -> Dict[str, Any]:
    # ensure repo root on path
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.backtest import run as run_backtest
    import pandas as pd

    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)

    params: Dict[str, Any] = {
        "risk": {
            "use_atr": True,
            "atr_window": 14,
            "atr_k_sl": 1.5,
            "atr_k_tp": float(atr_k_tp),
            "max_pos_pct": 0.01,
            "per_trade_sl_pct": 0.003,
            "tp_pct": 0.003,
            "dd_soft": 0.02,
            "dd_hard": 0.04,
        },
        "filters": {
            "ema_fast": int(ema_fast),
            "ema_slow": int(ema_slow),
            "min_atr_pct": float(atr_min),
            "max_atr_pct": 0.02,
        },
        "execution": {
            "micro_slices": 2,
            "time_stop_s": 3600,
            "trail_atr_k": 0.7,
        },
    }

    res = run_backtest(
        symbols=symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        timeframe=timeframe,
        strategy="momentum",
        params=params,
        maker_bps=2,
        taker_bps=5,
        slippage_bps=2,
        seed=42,
        out_dir=None,
        fast_mode=False,
    )
    out = dict(res.metrics)
    out.update(
        {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr_k_tp": atr_k_tp,
            "atr_min": atr_min,
            "sentiment_min": sent_min,
        }
    )
    days = max(1, int((end_ms - start_ms) / (24 * 3600 * 1000)))
    start_eq = 10_000.0
    out["daily_profit_floor"] = (float(out.get("net_pnl", 0.0)) / start_eq) / days
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="BTC/USDT,ETH/USDT")
    ap.add_argument("--timeframe", type=str, default="1h")
    ap.add_argument("--start", type=str)
    ap.add_argument("--end", type=str)
    ap.add_argument("--ema-fast", type=str, default="20,50")
    ap.add_argument("--ema-slow", type=str, default="50,200")
    ap.add_argument("--tp", type=str, default="2.0,2.5,3.0")
    ap.add_argument("--atr-min", type=str, default="0.0008,0.0015")
    ap.add_argument("--sent-min", type=str, default="0.0,0.1")
    ns = ap.parse_args()

    import pandas as pd

    end_ts = pd.Timestamp(ns.end) if ns.end else pd.Timestamp.utcnow().normalize()
    if getattr(end_ts, "tz", None) is None:
        end_ts = end_ts.tz_localize("UTC")
    else:
        end_ts = end_ts.tz_convert("UTC")
    start_ts = pd.Timestamp(ns.start) if ns.start else (end_ts - pd.Timedelta(days=30))
    if getattr(start_ts, "tz", None) is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")

    start = start_ts.strftime("%Y-%m-%d")
    end = end_ts.strftime("%Y-%m-%d")

    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    ema_fast_vals = [int(x) for x in ns.ema_fast.split(",") if x]
    ema_slow_vals = [int(x) for x in ns.ema_slow.split(",") if x]
    tp_vals = [float(x) for x in ns.tp.split(",") if x]
    atr_min_vals = [float(x) for x in ns.atr_min.split(",") if x]
    sent_min_vals = [float(x) for x in ns.sent_min.split(",") if x]

    results: List[Dict[str, Any]] = []
    for ef, es, tpv, am, sm in it.product(
        ema_fast_vals, ema_slow_vals, tp_vals, atr_min_vals, sent_min_vals
    ):
        r = run_bt(
            symbols=symbols,
            start=start,
            end=end,
            timeframe=ns.timeframe,
            ema_fast=ef,
            ema_slow=es,
            atr_k_tp=tpv,
            atr_min=am,
            sent_min=sm,
        )
        results.append(r)

    out_dir = Path("artifacts") / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    import csv

    csv_path = out_dir / f"sweep_{ts}.csv"
    if results:
        keys = [
            "ema_fast",
            "ema_slow",
            "atr_k_tp",
            "atr_min",
            "sentiment_min",
            "win_rate",
            "max_dd",
            "net_pnl",
            "trades",
            "daily_profit_floor",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for row in results:
                w.writerow({k: row.get(k, "") for k in keys})
    (out_dir / f"sweep_{ts}.json").write_text(json.dumps(results, indent=2))

    def meets(r: Dict[str, Any]) -> bool:
        try:
            return (
                float(r.get("win_rate", 0.0)) >= 0.65
                and float(r.get("max_dd", 1.0)) <= 0.20
                and float(r.get("daily_profit_floor", 0.0)) >= 0.01
            )
        except Exception:
            return False

    viable = [r for r in results if meets(r)]
    viable = sorted(
        viable,
        key=lambda x: (-x.get("daily_profit_floor", 0.0), -x.get("win_rate", 0.0)),
    )
    print(json.dumps({"viable_top": viable[:5], "all": results[:5]}, indent=2))
    print(f"wrote: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
