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
    logger.add(
        Path(log_dir) / "app.log",
        rotation="10 MB",
        retention="10 days",
        level=level,
        serialize=True,
    )


async def run_trader() -> None:
    load_dotenv()
    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)

    # Portfolio & brokers
    portfolio = Portfolio(
        maker_bps=settings.fees.maker_bps, taker_bps=settings.fees.taker_bps
    )
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
    prod_file = os.getenv(
        "STRATEGY_PARAMS_FILE",
        str(Path(settings.artifacts_dir) / "production_params.json"),
    )
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
                settings.risk.max_pos_pct = float(
                    risk_over.get("max_pos_pct", settings.risk.max_pos_pct)
                )
                settings.risk.per_trade_sl_pct = float(
                    risk_over.get("per_trade_sl_pct", settings.risk.per_trade_sl_pct)
                )
                settings.risk.tp_pct = float(
                    risk_over.get("tp_pct", settings.risk.tp_pct)
                )
                settings.risk.dd_soft = float(
                    risk_over.get("dd_soft", settings.risk.dd_soft)
                )
                settings.risk.dd_hard = float(
                    risk_over.get("dd_hard", settings.risk.dd_hard)
                )
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
        live_broker = CCXTBroker(
            settings.exchange,
            settings.api_key,
            settings.api_secret,
            settings.api_passphrase,
            True,
        )
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
        pos = {
            s: {
                "base": p.base,
                "avg_price": p.avg_price,
                "realized_pnl": p.realized_pnl,
            }
            for s, p in portfolio.positions.items()
        }
        return {
            "mode": settings.mode,
            "symbols": symbols,
            "balances": portfolio.balances,
            "positions": pos,
        }

    # Apply tuned profile at runtime
    def _apply_runtime_params(runtime: Dict[str, Any]) -> Dict[str, Any]:
        # Apply to live objects
        router.apply_params(runtime)
        r_over = runtime.get("risk", {})
        try:
            risk.max_pos_pct = float(r_over.get("max_pos_pct", risk.max_pos_pct))
            risk.per_trade_sl_pct = float(
                r_over.get("per_trade_sl_pct", risk.per_trade_sl_pct)
            )
            risk.tp_pct = float(r_over.get("tp_pct", risk.tp_pct))
        except Exception:
            pass
        return runtime

    def apply_profile() -> Dict[str, Any]:
        path = os.path.join(settings.artifacts_dir, "tuned_profile.json")
        import orjson

        if not os.path.exists(path):
            return {"applied": False, "reason": "no_tuned_profile"}
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
        params = data.get("params", {}) if isinstance(data, dict) else {}
        # Map tuned params to runtime strategy/risk knobs (best-effort)
        ma_n = int(params.get("ma_n", 50) or 50)
        _trend_ema = int(params.get("trend_ema", 0) or 0)
        atr_sl_k = float(params.get("atr_sl_k", 0.0) or 0.0)
        atr_tp_k = float(params.get("atr_tp_k", 0.0) or 0.0)
        risk_pct = float(params.get("risk_per_trade_pct", 0.0) or 0.0)

        # Derive additional knobs from tuned values
        min_range_bps = max(3, min(30, int(ma_n / 4)))
        mr_k = max(1.0, min(3.0, 1.5 + (atr_tp_k - atr_sl_k) * 0.5))
        micro_slices = (
            4
            if (risk_pct and risk_pct <= 0.006)
            else (3 if (risk_pct and risk_pct <= 0.012) else 2)
        )
        time_stop_s = (
            90
            if (atr_sl_k and atr_sl_k <= 1.0)
            else (150 if (atr_sl_k and atr_sl_k <= 2.0) else 180)
        )
        trail_atr_k = max(0.0, min(2.0, (atr_tp_k + atr_sl_k) / 2.0))
        retest_pct = 0.0 if not risk_pct else (0.002 if risk_pct <= 0.01 else 0.0)

        # Extract tuning meta
        metric_name = data.get("metric") if isinstance(data, dict) else None
        score_val = data.get("score") if isinstance(data, dict) else None
        from datetime import datetime as _dt

        applied_at = _dt.utcnow().isoformat() + "Z"

        runtime: Dict[str, Any] = {
            "momentum": {
                # Use MA window as a proxy for breakout window (scaled)
                "breakout_window": max(30, ma_n * 2),
                "min_range_bps": min_range_bps,
                "retest_pct": retest_pct,
            },
            "ml": {
                "enabled": bool((params.get("ml_enabled", False)) or False),
                "model_path": params.get(
                    "ml_model_path", "artifacts/models/ml_pipeline.joblib"
                ),
                "prob_cut": float(params.get("ml_prob_cut", 0.6) or 0.6),
            },
            "meanrev": {
                # Align mean-reversion window to MA
                "window": max(20, ma_n),
                "k": round(mr_k, 2),
            },
            "execution": {
                "micro_slices": micro_slices,
                "time_stop_s": time_stop_s,
                "trail_atr_k": trail_atr_k,
                "pyramid_max": 2 if risk_pct and risk_pct <= 0.012 else 1,
                "pyramid_step_pct": 0.005,
                "partial_r1": 1.0,
                "partial_r2": 1.5,
            },
            "filters": {
                "ema_fast": max(5, int((_trend_ema or 0) / 3)) if _trend_ema else 0,
                "ema_slow": int(_trend_ema or 0),
                "min_atr_pct": 0.001 if (risk_pct and risk_pct <= 0.01) else 0.0,
                "max_atr_pct": 0.05,
            },
            "risk": {
                # Approximate SL/TP from ATR multipliers for live per-trade SL/TP
                "per_trade_sl_pct": min(0.02, 0.003 * max(0.5, atr_sl_k)),
                "tp_pct": min(0.05, 0.006 * max(0.5, atr_tp_k)),
                "max_pos_pct": min(0.05, max(0.001, risk_pct))
                if risk_pct
                else settings.risk.max_pos_pct,
            },
            "tuning_meta": {
                "metric": metric_name,
                "score": score_val,
                "applied_at": applied_at,
            },
        }
        # Persist to production_params.json for reuse on restart (backup old)
        prod_path = os.path.join(settings.artifacts_dir, "production_params.json")
        prev_path = os.path.join(settings.artifacts_dir, "production_params.prev.json")
        os.makedirs(settings.artifacts_dir, exist_ok=True)
        # Backup previous if exists
        try:
            if os.path.exists(prod_path):
                import shutil as _sh

                _sh.copyfile(prod_path, prev_path)
        except Exception:
            pass
        with open(prod_path, "w", encoding="utf-8") as f:
            import json as _json

            _json.dump(runtime, f, indent=2)

        _apply_runtime_params(runtime)

        # Record to ledger
        try:
            ctx.ledger.append(
                {
                    "ts": 0,
                    "event": "profile_apply_runtime",
                    "params": runtime,
                    "source": "tuned_profile.json",
                    "metric": metric_name,
                    "score": score_val,
                    "applied_at": applied_at,
                }
            )
        except Exception:
            pass

        return {"applied": True, "runtime": runtime, "path": prod_path}

    def revert_profile() -> Dict[str, Any]:
        prev_path = os.path.join(settings.artifacts_dir, "production_params.prev.json")
        prod_path = os.path.join(settings.artifacts_dir, "production_params.json")
        if not os.path.exists(prev_path):
            return {"reverted": False, "reason": "no_backup"}
        import json as _json

        with open(prev_path, "r", encoding="utf-8") as f:
            runtime = _json.load(f)
        _apply_runtime_params(runtime)
        # restore backup as current
        with open(prod_path, "w", encoding="utf-8") as f:
            _json.dump(runtime, f, indent=2)
        try:
            ctx.ledger.append(
                {
                    "ts": 0,
                    "event": "profile_revert_runtime",
                    "params": runtime,
                    "source": "production_params.prev.json",
                }
            )
        except Exception:
            pass
        return {"reverted": True, "runtime": runtime}

    app = create_app(state_provider, apply_profile, revert_profile)

    async def http_server() -> None:
        config = uvicorn.Config(
            app, host="0.0.0.0", port=settings.port, log_level="info"
        )
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
