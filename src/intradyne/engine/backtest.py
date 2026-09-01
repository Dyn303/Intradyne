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

from intradyne.core.config import load_settings
from .data_loader import DataLoader, LoaderConfig, timeframe_to_seconds
from .portfolio import Portfolio
from loguru import logger

from intradyne.backtester.costs import assess
from .broker_paper import PaperBroker
from intradyne.core.ledger import ExplainabilityLedger
from .risk import RiskManager
from .execution import ExecContext, ExecutionManager
from .router import StrategyRouter
from intradyne.risk.shariah import assert_whitelisted


@dataclass
class BacktestResult:
    metrics: Dict[str, Any]
    run_id: str


def _annualization_factor(tf_seconds: int) -> float:
    # Approximate trading seconds per year
    return math.sqrt((365 * 24 * 3600) / max(1, tf_seconds))


def run(
    symbols: List[str],
    start_ms: int,
    end_ms: int,
    timeframe: str,
    strategy: str,
    params: Dict[str, Any],
    maker_bps: int,
    taker_bps: int,
    slippage_bps: int,
    seed: int = 42,
    out_dir: Optional[Path] = None,
    fast_mode: bool = False,
    early_target_trades_per_day: Optional[int] = None,
) -> BacktestResult:
    settings = load_settings()
    random.seed(seed)
    np.random.seed(seed)

    # Shariah whitelist enforcement
    wl = settings.load_symbols()
    for s in symbols:
        assert_whitelisted(s, wl)

    data_loader = DataLoader(
        LoaderConfig(data_dir=Path(settings.data_dir), exchange=settings.exchange)
    )

    _exec_cfg = (params.get("execution") or {}) if isinstance(params, dict) else {}
    _mode = str(_exec_cfg.get("execution_mode", settings.execution_mode))
    _offset = float(_exec_cfg.get("maker_offset_bps", settings.maker_offset_bps))
    _ttl = float(_exec_cfg.get("limit_ttl_s", settings.limit_ttl_s))

    portfolio = Portfolio(maker_bps=maker_bps, taker_bps=taker_bps)
    paper = PaperBroker(portfolio, slippage_bps=slippage_bps, limit_ttl_s=_ttl)
    ledger_path = Path(settings.artifacts_dir) / "backtests" / "ledger.jsonl"
    ledger = ExplainabilityLedger(path=str(ledger_path))

    risk = RiskManager(
        max_pos_pct=float(
            params.get("risk", {}).get("max_pos_pct", settings.risk.max_pos_pct)
        ),
        per_trade_sl_pct=float(
            params.get("risk", {}).get(
                "per_trade_sl_pct", settings.risk.per_trade_sl_pct
            )
        ),
        tp_pct=float(params.get("risk", {}).get("tp_pct", settings.risk.tp_pct)),
        dd_soft=float(params.get("risk", {}).get("dd_soft", settings.risk.dd_soft)),
        dd_hard=float(params.get("risk", {}).get("dd_hard", settings.risk.dd_hard)),
        flash_crash_drop_1h=settings.risk.flash_crash_drop_1h,
        max_concurrent_pos=settings.risk.max_concurrent_pos,
        kill_switch_breaches=settings.risk.kill_switch_breaches,
        use_atr=bool(params.get("risk", {}).get("use_atr", False)),
        atr_window=int(params.get("risk", {}).get("atr_window", 0)) or None,
        atr_k_sl=float(params.get("risk", {}).get("atr_k_sl", 0.0)) or None,
        atr_k_tp=float(params.get("risk", {}).get("atr_k_tp", 0.0)) or None,
    )

    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=ledger,
        whitelist=wl,
        live_broker=None,
        live_enabled=False,
        fast_mode=fast_mode,
        execution_mode=_mode,
        maker_offset_bps=_offset,
    )
    router_params = params or {}
    execman = ExecutionManager(ctx)
    router = StrategyRouter(symbols, risk, execman, portfolio, params=router_params)

    # The exit horizon must be long enough for the take-profit and stop-loss to
    # be reachable. The strategies were written for 1-second bars, where the
    # 120s default is 120 bars; on 1m bars it is two, and ~95% of positions
    # then close on the time stop before either level is touched. That
    # silently makes the whole tp/sl design inert, and a parameter sweep then
    # returns near-identical results for every configuration.
    _stop_bars = float(router.time_stop_s) / max(1, timeframe_to_seconds(timeframe))
    if _stop_bars < 3:
        logger.warning(
            f"time_stop_s={router.time_stop_s} is only {_stop_bars:.1f} bars at "
            f"{timeframe}: positions will close on the time stop before the "
            "take-profit or stop-loss is reached, and results will be "
            "insensitive to them"
        )

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
    #: Last realized PnL seen per symbol, so a change identifies a close.
    realized_seen: Dict[str, float] = {}
    #: Last traded price per symbol, for valuation and the closing liquidation.
    last_price: Dict[str, float] = {}
    #: Base held per symbol at the previous tick, to measure notional deployed.
    prev_base: Dict[str, float] = {}
    #: Total buy notional, the denominator for realised return.
    traded_notional = 0.0

    async def write_trade(event: Dict[str, Any]) -> None:
        if trades_fp is not None:
            trades_fp.write(orjson.dumps(event) + b"\n")

    async def loop() -> None:
        nonlocal \
            gross_profit, \
            gross_loss, \
            wins, \
            losses, \
            trades, \
            exposure_steps, \
            total_steps, \
            peak_equity, \
            traded_notional
        async for sym, bar in data_loader.multi_symbol_stream(
            symbols, timeframe, start_ms, end_ms
        ):
            # augment bar to l1 with symbol
            l1 = {**bar, "symbol": sym}
            # monitor exits and entries via router
            # Sweep resting limit orders against this quote before the router
            # acts: a passive order fills when the market comes to it, not
            # when it was placed.
            paper.on_tick(l1)
            await router.on_tick(l1)
            # compute equity/metrics
            total_steps += 1
            # Keep the last *price* per symbol. The closing liquidation below
            # needs it, and previously used eq_curve[-1] -- the portfolio
            # equity -- as though it were a price.
            if l1.get("last"):
                last_price[sym] = float(l1["last"])
            last_marks = {s: last_price.get(s, 0.0) for s in symbols}
            last_marks = {s: v for s, v in last_marks.items() if v > 0}
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
            # Count closed trades from realized-PnL movement.
            #
            # wins/losses were declared and marked nonlocal but never
            # incremented, so win_rate was always exactly 0.0 and
            # profit_factor always inf, whatever the strategy did. Every
            # summary.json ever produced carries those dead values.
            for sym_, pos_ in portfolio.positions.items():
                # Notional deployed, for realised return per unit of capital.
                _pb = prev_base.get(sym_, 0.0)
                if pos_.base > _pb:
                    traded_notional += (pos_.base - _pb) * last_price.get(sym_, 0.0)
                prev_base[sym_] = pos_.base

                prev = realized_seen.get(sym_, 0.0)
                curr = pos_.realized_pnl
                delta = curr - prev
                if delta == 0.0:
                    continue
                realized_seen[sym_] = curr
                if delta > 0:
                    wins += 1
                    gross_profit += delta
                else:
                    losses += 1
                    gross_loss += delta
                await write_trade(
                    {
                        "ts": bar.get("ts"),
                        "symbol": sym_,
                        "realized_pnl": delta,
                        "outcome": "win" if delta > 0 else "loss",
                        "equity": eq,
                    }
                )
        # After stream end, close any open positions at last price
        for sym, pos in portfolio.positions.items():
            if pos.base > 0:
                # Liquidate at the instrument's last traded price.
                #
                # This used eq_curve[-1], which is portfolio *equity*, not a
                # price. Every run that ended holding a position therefore
                # closed it at roughly the account value per unit: on ETH near
                # $1,875 that is a 5.3x windfall, and it fabricated the entire
                # reported profit of one run from a single fill. On a
                # higher-priced instrument it fabricates an equally large loss.
                mark = last_price.get(sym)
                if not mark:
                    continue
                l1 = {
                    "symbol": sym,
                    "bid": mark,
                    "ask": mark,
                    "last": mark,
                    "ts": end_ms / 1000.0,
                }
                await execman.submit(
                    sym,
                    "sell",
                    "market",
                    pos.base,
                    None,
                    l1,
                    "eod",
                    {},
                    {"whitelist": True, "spot_only": True, "long_only": True},
                )
        # The end-of-stream liquidation closes trades too; count them.
        for sym_, pos_ in portfolio.positions.items():
            delta = pos_.realized_pnl - realized_seen.get(sym_, 0.0)
            if delta == 0.0:
                continue
            realized_seen[sym_] = pos_.realized_pnl
            if delta > 0:
                wins += 1
                gross_profit += delta
            else:
                losses += 1
                gross_loss += delta

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
    std = (
        (sum((r - mean) ** 2 for r in rets) / max(1, len(rets))).__pow__(0.5)
        if rets
        else 0.0
    )
    neg = [r for r in rets if r < 0]
    std_neg = (
        (
            sum((r - (sum(neg) / len(neg) if neg else 0.0)) ** 2 for r in neg)
            / max(1, len(neg))
        ).__pow__(0.5)
        if neg
        else 0.0
    )
    sharpe = (mean / (std or 1e-9)) * ann if rets else 0.0
    sortino = (mean / (std_neg or 1e-9)) * ann if rets else 0.0
    max_dd = 0.0
    peak = 0.0
    for v in eq_curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    exposure = exposure_steps / max(1, total_steps)

    round_trips = wins + losses
    # dd_soft latches and is never reset, so once equity dips below it the
    # strategy stops opening positions for the remainder of the window. Runs
    # therefore end at roughly -dd_soft regardless of configuration, which is
    # why net_pnl looks nearly identical across a sweep and cannot be used to
    # compare configurations. Expectancy per trade can.
    halted_early = bool(risk.state.dd_soft_triggered or risk.state.dd_hard_triggered)
    # Mixed by design: counts, rates, strings and the nested edge dict.
    summary: Dict[str, Any] = {
        # `trades` counts individual fills; a round trip is typically several
        # (both strategies can enter on one tick, and exits are separate
        # orders). They were reported side by side as though comparable, so
        # the sample size read several times larger than it was.
        "fills": ctx.trades,
        "trades": round_trips,
        "round_trips": round_trips,
        "win_rate": wins / max(1, round_trips),
        "gross_pnl": portfolio.get_position(symbols[0]).realized_pnl
        if symbols
        else 0.0,
        "net_pnl": pnl,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "wins": wins,
        "losses": losses,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        # None rather than inf when there were no losses: infinity here has
        # always meant "no data", not "flawless strategy".
        "profit_factor": (gross_profit / abs(gross_loss))
        if gross_loss < 0
        else (None if wins == 0 else float("inf")),
        "exposure_time": exposure,
        "final_equity": portfolio.equity({}),
        # True when the drawdown guard stopped new entries before the window
        # ended: the sample is then truncated, not a full-period result.
        "halted_early": halted_early,
        "halt_reason": (
            "dd_hard"
            if risk.state.dd_hard_triggered
            else ("dd_soft" if risk.state.dd_soft_triggered else None)
        ),
    }

    # A win rate means nothing without the breakeven it has to clear. At a
    # 20bps take-profit against a 30bps stop, 14bps of round-trip cost implies
    # a ~88% breakeven -- so a 70% win rate looks strong and loses money. The
    # two are reported together so they cannot be read apart.
    # Realised return per unit of capital deployed. The assessment below
    # answers "what win rate would this geometry need *if* every win were
    # exactly tp and every loss exactly sl" -- a target-setting question. It
    # is not a measurement: exits do not respect those levels (a stop can gap
    # through, a target may never be touched), so a run can show positive
    # theoretical expectancy while losing money. Both are reported, and they
    # disagreeing is the signal that the exits are not honouring the levels.
    realized_bps = (pnl / traded_notional * 1e4) if traded_notional > 0 else 0.0
    summary["traded_notional"] = traded_notional
    summary["realized_return_bps"] = realized_bps

    summary["edge"] = assess(
        win_rate=summary["win_rate"],
        tp_pct=risk.tp_pct,
        sl_pct=risk.per_trade_sl_pct,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        maker_bps=maker_bps,
        trades=round_trips,
    ).to_dict()
    summary["edge"]["realized_return_bps"] = realized_bps
    # A theoretical edge that the realised result contradicts is not an edge.
    if summary["edge"]["verdict"] in {"clears_with_margin", "marginal"} and (
        realized_bps <= 0
    ):
        summary["edge"]["verdict"] = "contradicted_by_realized"
        summary["edge"]["note"] = (
            f"geometry implies a positive edge but the run realised "
            f"{realized_bps:+.2f} bps per unit of capital: exits are not "
            "honouring the configured levels"
        )

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return BacktestResult(summary, run_id)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True, help="Comma-separated symbols")
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument(
        "--strategy", type=str, choices=["momentum", "meanrev"], default="momentum"
    )
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
    run(
        symbols,
        start_ms,
        end_ms,
        ns.timeframe,
        ns.strategy,
        params,
        ns.fees_maker_bps,
        ns.fees_taker_bps,
        ns.slippage_bps,
        ns.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
