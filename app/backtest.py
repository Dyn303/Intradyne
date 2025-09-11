from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import orjson

from .config import load_settings
from .data_loader import DataLoader, LoaderConfig, timeframe_to_seconds
from .portfolio import Portfolio
from .broker_paper import PaperBroker
from .ledger import ExplainabilityLedger
from .risk import RiskManager
from .execution import ExecContext, ExecutionManager
from .router import StrategyRouter
from .compliance import assert_whitelisted


@dataclass
class BacktestResult:
    metrics: Dict[str, Any]
    run_id: str


def _annualization_factor(tf_seconds: int) -> float:
    # Approximate trading seconds per year
    return math.sqrt((365 * 24 * 3600) / max(1, tf_seconds))


def run(symbols: List[str], start_ms: int, end_ms: int, timeframe: str, strategy: str, params: Dict[str, Any], maker_bps: int, taker_bps: int, slippage_bps: int, seed: int = 42, out_dir: Optional[Path] = None, fast_mode: bool = False, early_target_trades_per_day: Optional[int] = None) -> BacktestResult:
    settings = load_settings()
    random.seed(seed)
    np.random.seed(seed)

    # Shariah whitelist enforcement
    wl = settings.load_symbols()
    for s in symbols:
        assert_whitelisted(s, wl)

    data_loader = DataLoader(LoaderConfig(data_dir=Path(settings.data_dir), exchange=settings.exchange))

    portfolio = Portfolio(maker_bps=maker_bps, taker_bps=taker_bps)
    paper = PaperBroker(portfolio, slippage_bps=slippage_bps)
    ledger_path = Path(settings.artifacts_dir) / "backtests" / "ledger.jsonl"
    ledger = ExplainabilityLedger(path=str(ledger_path))

    risk = RiskManager(
        max_pos_pct=float(params.get("risk", {}).get("max_pos_pct", settings.risk.max_pos_pct)),
        per_trade_sl_pct=float(params.get("risk", {}).get("per_trade_sl_pct", settings.risk.per_trade_sl_pct)),
        tp_pct=float(params.get("risk", {}).get("tp_pct", settings.risk.tp_pct)),
        dd_soft=float(params.get("risk", {}).get("dd_soft", settings.risk.dd_soft)),
        dd_hard=float(params.get("risk", {}).get("dd_hard", settings.risk.dd_hard)),
        flash_crash_drop_1h=settings.risk.flash_crash_drop_1h,
        max_concurrent_pos=settings.risk.max_concurrent_pos,
        kill_switch_breaches=settings.risk.kill_switch_breaches,
    )

    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=ledger,
        whitelist=wl,
        live_broker=None,
        live_enabled=False,
        fast_mode=fast_mode,
    )
    strat_params = {strategy: params.get(strategy, {})}
    execman = ExecutionManager(ctx)
    router = StrategyRouter(symbols, risk, execman, portfolio, params=strat_params)

    # Prepare artifacts dirs
    run_id = f"{strategy}_{int(time.time())}_{seed}"
    out_dir = out_dir or (Path(settings.artifacts_dir) / "backtests" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_fp = None if fast_mode else (out_dir / "trades.jsonl").open("wb")

    # Backtest loop: merged stream
    tf_sec = timeframe_to_seconds(timeframe)
    eq_curve: List[float] = []
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    trades = 0
    exposure_steps = 0
    total_steps = 0
    start_equity = portfolio.equity()
    peak_equity = start_equity

    async def write_trade(event: Dict[str, Any]) -> None:
        if trades_fp is not None:
            trades_fp.write(orjson.dumps(event) + b"\n")

    async def loop() -> None:
        nonlocal gross_profit, gross_loss, wins, losses, trades, exposure_steps, total_steps, peak_equity
        async for sym, bar in data_loader.multi_symbol_stream(symbols, timeframe, start_ms, end_ms):
            # augment bar to l1 with symbol
            l1 = {**bar, "symbol": sym}
            # monitor exits and entries via router
            await router.on_tick(l1)
            # compute equity/metrics
            total_steps += 1
            last_marks = {s: l1["last"] for s in symbols}
            eq = portfolio.equity(last_marks)
            eq_curve.append(eq)
            if any(p.base > 0 for p in portfolio.positions.values()):
                exposure_steps += 1
            # drawdown update
            peak_equity = max(peak_equity, eq)
            if eq < start_equity * (1 - risk.dd_hard):
                risk.state.dd_hard_triggered = True
            risk.update_drawdown(start_equity, eq)
            # Early trade-rate pruning (optional)
            if early_target_trades_per_day and (end_ms > start_ms):
                elapsed = (bar["ts"] * 1000 - start_ms) / (end_ms - start_ms)
                if elapsed > 0.1:  # wait at least 10% of window
                    target_so_far = early_target_trades_per_day * elapsed
                    if ctx.trades < target_so_far * 0.5:  # behind pace
                        raise RuntimeError("EARLY_PRUNE_TRADES")
            # capture realized pnl changes via portfolio positions updates
            # We infer fills via ledger writes (paper fills). Not strictly needed for metrics here.
        # After stream end, close any open positions at last price
        for sym, pos in portfolio.positions.items():
            if pos.base > 0:
                l1 = {"symbol": sym, "bid": eq_curve[-1], "ask": eq_curve[-1], "last": eq_curve[-1], "ts": end_ms / 1000.0}
                await execman.submit(sym, "sell", "market", pos.base, None, l1, "eod", {}, {"whitelist": True, "spot_only": True, "long_only": True})

    # Run event loop
    import asyncio

    try:
        asyncio.run(loop())
    except RuntimeError as e:
        if str(e) == "EARLY_PRUNE_TRADES":
            # bubble up for optimizer to prune
            raise
        else:
            raise
    finally:
        if trades_fp is not None:
            trades_fp.close()

    # Compute metrics
    pnl = portfolio.balances.get(portfolio.quote_ccy, 0.0) - start_equity
    rets: List[float] = []
    for i in range(1, len(eq_curve)):
        prev = eq_curve[i - 1]
        cur = eq_curve[i]
        if prev > 0:
            rets.append((cur / prev - 1.0))
    ann = _annualization_factor(tf_sec)
    mean = (sum(rets) / len(rets)) if rets else 0.0
    std = (sum((r - mean) ** 2 for r in rets) / max(1, len(rets))).__pow__(0.5) if rets else 0.0
    neg = [r for r in rets if r < 0]
    std_neg = (sum((r - (sum(neg) / len(neg) if neg else 0.0)) ** 2 for r in neg) / max(1, len(neg))).__pow__(0.5) if neg else 0.0
    sharpe = (mean / (std or 1e-9)) * ann if rets else 0.0
    sortino = (mean / (std_neg or 1e-9)) * ann if rets else 0.0
    max_dd = 0.0
    peak = 0.0
    for v in eq_curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    exposure = exposure_steps / max(1, total_steps)

    summary = {
        "trades": ctx.trades,
        "win_rate": wins / max(1, wins + losses),
        "gross_pnl": portfolio.get_position(symbols[0]).realized_pnl if symbols else 0.0,
        "net_pnl": pnl,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "profit_factor": (gross_profit / abs(gross_loss)) if gross_loss < 0 else float("inf"),
        "exposure_time": exposure,
        "final_equity": portfolio.equity({}),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return BacktestResult(summary, run_id)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True, help="Comma-separated symbols")
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument("--strategy", type=str, choices=["momentum", "meanrev"], default="momentum")
    p.add_argument("--params", type=str, default="{}")
    p.add_argument("--fees-maker-bps", type=int, default=2)
    p.add_argument("--fees-taker-bps", type=int, default=5)
    p.add_argument("--slippage-bps", type=int, default=2)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args()
    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start_ms = int(pd.Timestamp(ns.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(ns.end, tz="UTC").timestamp() * 1000)
    try:
        params = json.loads(ns.params)
    except Exception:
        params = {}
    run(symbols, start_ms, end_ms, ns.timeframe, ns.strategy, params, ns.fees_maker_bps, ns.fees_taker_bps, ns.slippage_bps, ns.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
