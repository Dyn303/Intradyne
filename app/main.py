from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from loguru import logger
import uvicorn

from .config import load_settings
from .data_ws import DataFeed
from .portfolio import Portfolio
from .broker_paper import PaperBroker
from .broker_ccxt import CCXTBroker
from .execution import ExecContext, ExecutionManager
from .ledger import ExplainabilityLedger
from .risk import RiskManager
from .router import StrategyRouter
from .server import create_app


def setup_logging(log_dir: str, level: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level=level, serialize=True)
    logger.add(Path(log_dir) / "app.log", rotation="10 MB", retention="10 days", level=level, serialize=True)


async def run_trader() -> None:
    load_dotenv()
    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)

    # Portfolio & brokers
    portfolio = Portfolio(maker_bps=settings.fees.maker_bps, taker_bps=settings.fees.taker_bps)
    paper = PaperBroker(portfolio, slippage_bps=settings.fees.slippage_bps)
    ledger = ExplainabilityLedger(path=os.path.join(settings.log_dir, "ledger.jsonl"))

    # Markets & symbols
    # Load and filter by exchange markets using ccxt
    import ccxt.async_support as ccxt

    ex = getattr(ccxt, settings.exchange)()
    markets = await ex.load_markets()
    await ex.close()
    symbols_available = list(markets.keys())
    symbols = settings.load_symbols(symbols_available)

    # Load optional production params to tune strategies and risk
    strategy_params: Dict[str, Any] | None = None
    prod_file = os.getenv("STRATEGY_PARAMS_FILE", str(Path(settings.artifacts_dir) / "production_params.json"))
    if os.path.exists(prod_file):
        try:
            with open(prod_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Accept already nested dict
            strategy_params = {}
            for k in ("momentum", "meanrev"):
                if k in raw and isinstance(raw[k], dict):
                    strategy_params[k] = raw[k]
            # Risk overrides (optional)
            risk_over = raw.get("risk", {}) if isinstance(raw, dict) else {}
            if risk_over:
                settings.risk.max_pos_pct = float(risk_over.get("max_pos_pct", settings.risk.max_pos_pct))
                settings.risk.per_trade_sl_pct = float(risk_over.get("per_trade_sl_pct", settings.risk.per_trade_sl_pct))
                settings.risk.tp_pct = float(risk_over.get("tp_pct", settings.risk.tp_pct))
                settings.risk.dd_soft = float(risk_over.get("dd_soft", settings.risk.dd_soft))
                settings.risk.dd_hard = float(risk_over.get("dd_hard", settings.risk.dd_hard))
            logger.info(f"Loaded strategy params from {prod_file}")
        except Exception as e:
            logger.warning(f"Failed to load strategy params {prod_file}: {e}")

    risk = RiskManager(
        max_pos_pct=settings.risk.max_pos_pct,
        per_trade_sl_pct=settings.risk.per_trade_sl_pct,
        tp_pct=settings.risk.tp_pct,
        dd_soft=settings.risk.dd_soft,
        dd_hard=settings.risk.dd_hard,
        flash_crash_drop_1h=settings.risk.flash_crash_drop_1h,
        max_concurrent_pos=settings.risk.max_concurrent_pos,
        kill_switch_breaches=settings.risk.kill_switch_breaches,
    )

    live_broker = None
    if settings.mode == "live" and settings.live_trading_enabled:
        live_broker = CCXTBroker(settings.exchange, settings.api_key, settings.api_secret, settings.api_passphrase, True)
        await live_broker.connect()

    ctx = ExecContext(
        portfolio=portfolio,
        paper=paper,
        ledger=ledger,
        whitelist=symbols,
        live_broker=live_broker,
        live_enabled=(settings.mode == "live" and settings.live_trading_enabled),
    )
    execman = ExecutionManager(ctx)
    router = StrategyRouter(symbols, risk, execman, portfolio, params=strategy_params)

    # FastAPI app
    def state_provider() -> Dict[str, Any]:
        pos = {s: {"base": p.base, "avg_price": p.avg_price, "realized_pnl": p.realized_pnl} for s, p in portfolio.positions.items()}
        return {
            "mode": settings.mode,
            "symbols": symbols,
            "balances": portfolio.balances,
            "positions": pos,
        }

    app = create_app(state_provider)

    async def http_server() -> None:
        config = uvicorn.Config(app, host="0.0.0.0", port=settings.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def trading_loop() -> None:
        feed = DataFeed(settings.exchange, use_testnet=settings.use_testnet)
        async for l1 in feed.start(symbols):
            await router.on_tick(l1)

    await asyncio.gather(http_server(), trading_loop())


def main() -> None:
    asyncio.run(run_trader())


if __name__ == "__main__":
    main()
