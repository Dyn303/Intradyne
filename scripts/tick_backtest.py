#!/usr/bin/env python
"""Backtest against real trade ticks, with a genuine bid/ask.

    python scripts/tick_backtest.py --file ETHUSDT-aggTrades-2026-08-28.csv \\
        --symbol ETH/USDT --hours 4 --mode taker,maker

The bar backtest cannot answer whether maker execution helps: OHLCV carries no
bid/ask, so `bid = ask = last` and a resting order is filled by any downtick,
which overstates adverse selection. Binance aggTrades carries
`was_buyer_maker`, giving the aggressor side of every trade:

    was_buyer_maker = False  ->  a buyer lifted the offer   ->  traded at the ask
    was_buyer_maker = True   ->  a seller hit the bid       ->  traded at the bid

Tracking the most recent of each reconstructs a real L1 quote, and a resting
bid then fills only when an aggressive seller actually trades down to it --
which is how a maker fill really happens.

Ticks drive `StrategyRouter.on_tick` directly, so the strategy sees the
microstructure it was written for rather than a bar summary.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loguru import logger  # noqa: E402

from intradyne.backtester.costs import assess  # noqa: E402

# Per-fill logging dominates runtime over a million ticks.
logger.remove()


def read_ticks(path: Path, symbol: str, hours: float) -> Iterator[Dict[str, Any]]:
    """Yield L1 quotes reconstructed from aggregated trades."""
    bid: float | None = None
    ask: float | None = None
    t0: float | None = None
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 7:
                continue
            try:
                price = float(row[1])
                # Binance publishes these in microseconds.
                ts = float(row[5]) / 1e6
                buyer_was_maker = row[6].strip().lower() in ("true", "1")
            except (ValueError, IndexError):
                continue
            if t0 is None:
                t0 = ts
            if hours and (ts - t0) > hours * 3600.0:
                return
            if buyer_was_maker:
                bid = price  # a seller hit the bid
            else:
                ask = price  # a buyer lifted the offer
            if bid is None or ask is None:
                continue
            yield {
                "symbol": symbol,
                "ts": ts,
                "bid": bid,
                "ask": ask,
                "last": price,
                "volume": float(row[2]),
            }


def run_mode(
    ticks: List[Dict[str, Any]],
    symbol: str,
    mode: str,
    tp_pct: float,
    sl_pct: float,
    time_stop_s: int,
    limit_ttl_s: float,
    fees: Dict[str, int],
) -> Dict[str, Any]:
    import asyncio

    from intradyne.core.ledger import Ledger
    from intradyne.engine.broker_paper import PaperBroker
    from intradyne.engine.execution import ExecContext, ExecutionManager
    from intradyne.engine.portfolio import Portfolio
    from intradyne.engine.risk import RiskManager
    from intradyne.engine.router import StrategyRouter

    portfolio = Portfolio(maker_bps=fees["maker"], taker_bps=fees["taker"])
    paper = PaperBroker(
        portfolio, slippage_bps=fees["slippage"], limit_ttl_s=limit_ttl_s
    )
    risk = RiskManager(
        max_pos_pct=0.015,
        per_trade_sl_pct=sl_pct,
        tp_pct=tp_pct,
        dd_soft=0.03,
        dd_hard=0.05,
        flash_crash_drop_1h=0.30,
        max_concurrent_pos=5,
        kill_switch_breaches=3,
    )
    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=Ledger(path=str(Path(tempfile.mkdtemp()) / "l.jsonl")),
        whitelist=[symbol],
        fast_mode=True,
        execution_mode=mode,
        maker_offset_bps=0.0,
    )
    router = StrategyRouter([symbol], risk, ExecutionManager(ctx), portfolio)
    router.time_stop_s = time_stop_s

    start_equity = portfolio.equity()
    wins = losses = 0
    gross_profit = gross_loss = 0.0
    traded_notional = 0.0
    seen_pnl = 0.0
    prev_base = 0.0
    maker_fills = taker_fills = 0

    original_execute = PaperBroker._execute

    def _count(self, order, qty, price, is_maker):
        nonlocal maker_fills, taker_fills
        if is_maker:
            maker_fills += 1
        else:
            taker_fills += 1
        return original_execute(self, order, qty, price, is_maker)

    PaperBroker._execute = _count
    try:

        async def drive() -> None:
            nonlocal wins, losses, gross_profit, gross_loss
            nonlocal traded_notional, seen_pnl, prev_base
            for l1 in ticks:
                paper.on_tick(l1)
                await router.on_tick(l1)
                pos = portfolio.get_position(symbol)
                if pos.base > prev_base:
                    traded_notional += (pos.base - prev_base) * l1["last"]
                prev_base = pos.base
                delta = pos.realized_pnl - seen_pnl
                if delta:
                    seen_pnl = pos.realized_pnl
                    if delta > 0:
                        wins += 1
                        gross_profit += delta
                    else:
                        losses += 1
                        gross_loss += delta

        asyncio.run(drive())
    finally:
        PaperBroker._execute = original_execute

    # Close out at the last traded price, never at an equity value.
    pos = portfolio.get_position(symbol)
    if pos.base > 0 and ticks:
        portfolio.sell(symbol, pos.base, ticks[-1]["bid"], is_maker=False)
        delta = pos.realized_pnl - seen_pnl
        if delta > 0:
            wins += 1
            gross_profit += delta
        elif delta < 0:
            losses += 1
            gross_loss += delta

    net = portfolio.balances[portfolio.quote_ccy] - start_equity
    trips = wins + losses
    realized_bps = (net / traded_notional * 1e4) if traded_notional > 0 else 0.0
    edge = assess(
        win_rate=wins / max(1, trips),
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        taker_bps=fees["taker"],
        slippage_bps=fees["slippage"],
        maker_bps=fees["maker"],
        trades=trips,
    )
    return {
        "mode": mode,
        "round_trips": trips,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / max(1, trips),
        "net_pnl": net,
        "traded_notional": traded_notional,
        "realized_bps": realized_bps,
        "breakeven": edge.breakeven_win_rate,
        "maker_fills": maker_fills,
        "taker_fills": taker_fills,
        "verdict": edge.verdict,
    }


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", required=True)
    p.add_argument("--symbol", default="ETH/USDT")
    p.add_argument("--hours", type=float, default=4.0)
    p.add_argument("--mode", default="taker,maker")
    p.add_argument("--tp-bps", type=float, default=20.0)
    p.add_argument("--sl-bps", type=float, default=30.0)
    p.add_argument("--time-stop-s", type=int, default=120)
    p.add_argument("--limit-ttl-s", type=float, default=60.0)
    p.add_argument("--maker-bps", type=int, default=2)
    p.add_argument("--taker-bps", type=int, default=5)
    p.add_argument("--slippage-bps", type=int, default=2)
    args = p.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"no such file: {path}")
        return 1

    print(f"loading ticks from {path.name} ({args.hours:g}h) ...", end=" ", flush=True)
    ticks = list(read_ticks(path, args.symbol, args.hours))
    if not ticks:
        print("none")
        return 1
    span = (ticks[-1]["ts"] - ticks[0]["ts"]) / 3600.0
    spreads = [(t["ask"] - t["bid"]) / t["last"] * 1e4 for t in ticks[::500]]
    spreads = [s for s in spreads if s >= 0]
    med_spread = sorted(spreads)[len(spreads) // 2] if spreads else 0.0
    print(f"{len(ticks):,} quotes over {span:.2f}h, median spread {med_spread:.2f} bps")

    fees = {
        "maker": args.maker_bps,
        "taker": args.taker_bps,
        "slippage": args.slippage_bps,
    }
    print(
        f"\ntp={args.tp_bps:g}bps sl={args.sl_bps:g}bps "
        f"time_stop={args.time_stop_s}s limit_ttl={args.limit_ttl_s:g}s\n"
    )
    header = (
        f"{'mode':7} {'trips':>6} {'win':>7} {'b/e':>6} {'realised':>10} "
        f"{'net pnl':>9} {'maker':>7} {'taker':>7}  verdict"
    )
    print(header)
    print("-" * len(header))
    for mode in [m.strip() for m in args.mode.split(",") if m.strip()]:
        r = run_mode(
            ticks,
            args.symbol,
            mode,
            args.tp_bps / 1e4,
            args.sl_bps / 1e4,
            args.time_stop_s,
            args.limit_ttl_s,
            fees,
        )
        be = f"{r['breakeven']:6.0%}" if r["breakeven"] is not None else "     -"
        print(
            f"{r['mode']:7} {r['round_trips']:6d} {r['win_rate']:7.1%} {be} "
            f"{r['realized_bps']:+9.2f}b {r['net_pnl']:+9.2f} "
            f"{r['maker_fills']:7d} {r['taker_fills']:7d}  {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
