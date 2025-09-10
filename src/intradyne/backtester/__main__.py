from __future__ import annotations

import argparse
import random
from pathlib import Path

from src.backtester.engine import run_backtest as run_engine  # type: ignore[attr-defined]
from intradyne.core.logging import setup_logging
from src.backtester.engine import run_backtest_advanced


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="intradyne.backtester")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--mode", type=str, choices=["daily","hybrid","trend1h"], default="daily")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--symbols", type=str, default="BTC/USDT,ETH/USDT")
    p.add_argument("--auto-topk", type=int, default=0, dest="auto_topk", help="If >0, select top-K symbols by momentum from whitelist")
    p.add_argument("--ledger", type=str, default="explainability_ledger.jsonl")
    p.add_argument("--starting-equity", type=float, default=10000.0)
    # Strategy params
    p.add_argument("--ma", type=int, default=20, help="Daily MA window (days)")
    p.add_argument("--ma1h-fast", type=int, default=10, dest="ma1h_fast", help="Hybrid 1h fast MA")
    p.add_argument("--ma1h-slow", type=int, default=30, dest="ma1h_slow", help="Hybrid 1h slow MA")
    p.add_argument("--ma15m", type=int, default=10, help="Hybrid scalp TF fast MA (applies to chosen scalp TF)")
    p.add_argument("--scalp-tf", type=str, default="15m", dest="scalp_tf", help="Scalping timeframe: 15m or 5m")
    p.add_argument("--scalp-dev-pct", type=float, default=0.005, dest="scalp_dev_pct", help="Scalp entry deviation below 15m MA (e.g., 0.005=0.5%)")
    p.add_argument("--scalp-hold", type=int, default=8, dest="scalp_hold_bars", help="Max 15m bars to hold a scalp trade")
    p.add_argument("--scalp-alloc", type=float, default=0.10, dest="scalp_alloc_frac", help="Fraction of equity to allocate per scalp trade (0-1)")
    p.add_argument("--scalp-cooldown", type=int, default=8, dest="scalp_cooldown_bars", help="Bars to wait after a scalp exit before next scalp")
    p.add_argument("--scalp-z", type=float, default=1.5, dest="scalp_z_thresh", help="Z-score threshold below which to allow scalps (negative z)")
    p.add_argument("--scalp-tp-pct", type=float, default=0.003, dest="scalp_tp_pct", help="Take profit percent (e.g., 0.003 = 0.3%)")
    p.add_argument("--scalp-sl-pct", type=float, default=0.004, dest="scalp_sl_pct", help="Stop loss percent (e.g., 0.004 = 0.4%)")
    p.add_argument("--slippage-bps", type=float, default=2.0, dest="slippage_bps", help="Per-side slippage in basis points")
    p.add_argument("--scalp-max-per-day", type=int, default=8, dest="scalp_max_per_day", help="Max scalp entries per day per symbol")
    p.add_argument("--atr-min-pct", type=float, default=0.003, dest="atr_min_pct", help="Min volatility (std of returns) for scalps")
    p.add_argument("--atr-max-pct", type=float, default=0.02, dest="atr_max_pct", help="Max volatility for scalps")
    p.add_argument("--tod-start", type=int, default=6, dest="tod_start_utc", help="UTC hour to start allowing scalps")
    p.add_argument("--tod-end", type=int, default=22, dest="tod_end_utc", help="UTC hour to stop allowing scalps")
    p.add_argument("--profile", type=str, default="default", help="Profile presets: default or aggressive-sim")
    p.add_argument("--sweep-highfreq", action="store_true", help="Run a small parameter sweep for high-frequency hybrid mode")
    args = p.parse_args(argv)

    setup_logging()
    random.seed(args.seed)
    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.auto_topk and args.auto_topk > 0:
        # Momentum selector: top-K by 60-day return among allowed whitelist
        from src.core.config import load_settings
        from src.backtester.engine import get_candles, _symbol_to_pair
        s = load_settings()
        wl = s.allowed_crypto_list()
        pairs = [_symbol_to_pair(x) for x in wl]
        scores = []
        for sym in pairs:
            cds = get_candles(sym, 60, allow_net=True)
            if len(cds) >= 2:
                r = (cds[-1].close / max(1e-9, cds[0].close)) - 1.0
                scores.append((r, sym))
        scores.sort(reverse=True, key=lambda t: t[0])
        symbols = [sym for _, sym in scores[: int(args.auto_topk)]] or symbols
    if args.mode == "hybrid":
        from src.backtester.engine import run_backtest_hybrid

        # Profile overrides for aggressive-sim
        if args.profile == "aggressive-sim":
            args.scalp_tf = "5m"
            args.scalp_dev_pct = min(args.scalp_dev_pct, 0.001)
            args.scalp_z_thresh = min(args.scalp_z_thresh, 0.5)
            args.scalp_hold_bars = min(args.scalp_hold_bars, 2)
            args.scalp_cooldown_bars = 0
            args.scalp_alloc_frac = min(args.scalp_alloc_frac, 0.005)
            args.scalp_max_per_day = max(args.scalp_max_per_day, 40)
            args.atr_min_pct = min(args.atr_min_pct, 0.001)
            args.atr_max_pct = max(args.atr_max_pct, 0.03)
            args.tod_start_utc, args.tod_end_utc = 0, 24
            args.slippage_bps = max(args.slippage_bps, 5.0)

        if args.sweep_highfreq:
            # Simple sweep: vary dev, z, hold, alloc; keep 5m, high cap
            devs = [0.001, 0.002]
            zs = [0.5, 1.0]
            holds = [2, 3]
            allocs = [0.005, 0.01]
            for d in devs:
                for z in zs:
                    for hb in holds:
                        for al in allocs:
                            run_backtest_hybrid(
                                days=args.days,
                                symbols=symbols,
                                starting_equity=args.starting_equity,
                                allow_net=True,
                                ma_fast_1h=args.ma1h_fast,
                                ma_slow_1h=args.ma1h_slow,
                                ma_fast_15m=args.ma15m,
                                scalp_tf=args.scalp_tf,
                                scalp_dev_pct=d,
                                scalp_hold_bars=hb,
                                scalp_alloc_frac=al,
                                scalp_cooldown_bars=0,
                                scalp_z_thresh=z,
                                scalp_tp_pct=args.scalp_tp_pct,
                                scalp_sl_pct=args.scalp_sl_pct,
                                slippage_bps=args.slippage_bps,
                                scalp_max_per_day=max(60, args.scalp_max_per_day),
                                atr_min_pct=args.atr_min_pct,
                                atr_max_pct=args.atr_max_pct,
                                tod_start_utc=0,
                                tod_end_utc=24,
                            )
            # Note: sweep writes multiple reports; see /app/reports
            summary, csv_path, json_path = run_backtest_hybrid(
                days=args.days,
                symbols=symbols,
                starting_equity=args.starting_equity,
                allow_net=True,
                ma_fast_1h=args.ma1h_fast,
                ma_slow_1h=args.ma1h_slow,
                ma_fast_15m=args.ma15m,
                scalp_tf=args.scalp_tf,
                scalp_dev_pct=args.scalp_dev_pct,
                scalp_hold_bars=args.scalp_hold_bars,
                scalp_alloc_frac=args.scalp_alloc_frac,
                scalp_cooldown_bars=args.scalp_cooldown_bars,
                scalp_z_thresh=args.scalp_z_thresh,
                scalp_tp_pct=args.scalp_tp_pct,
                scalp_sl_pct=args.scalp_sl_pct,
                slippage_bps=args.slippage_bps,
                scalp_max_per_day=args.scalp_max_per_day,
                atr_min_pct=args.atr_min_pct,
                atr_max_pct=args.atr_max_pct,
                tod_start_utc=args.tod_start_utc,
                tod_end_utc=args.tod_end_utc,
            )
        else:
            summary, csv_path, json_path = run_backtest_hybrid(
                days=args.days,
                symbols=symbols,
                starting_equity=args.starting_equity,
                allow_net=True,
                ma_fast_1h=args.ma1h_fast,
                ma_slow_1h=args.ma1h_slow,
                ma_fast_15m=args.ma15m,
                scalp_tf=args.scalp_tf,
                scalp_dev_pct=args.scalp_dev_pct,
                scalp_hold_bars=args.scalp_hold_bars,
                scalp_alloc_frac=args.scalp_alloc_frac,
                scalp_cooldown_bars=args.scalp_cooldown_bars,
                scalp_z_thresh=args.scalp_z_thresh,
                scalp_tp_pct=args.scalp_tp_pct,
                scalp_sl_pct=args.scalp_sl_pct,
                slippage_bps=args.slippage_bps,
                scalp_max_per_day=args.scalp_max_per_day,
                atr_min_pct=args.atr_min_pct,
                atr_max_pct=args.atr_max_pct,
                tod_start_utc=args.tod_start_utc,
                tod_end_utc=args.tod_end_utc,
            )
    elif args.mode == "trend1h":
        from src.backtester.engine import run_backtest_trend1h

        summary, csv_path, json_path = run_backtest_trend1h(
            days=args.days,
            symbols=symbols,
            starting_equity=args.starting_equity,
            allow_net=True,
            ma_fast_1h=args.ma1h_fast,
            ma_slow_1h=args.ma1h_slow,
            slippage_bps=args.slippage_bps,
        )
    else:
        summary, csv_path, json_path = run_backtest_advanced(days=args.days, symbols=symbols, starting_equity=args.starting_equity, ma_window=args.ma)
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
