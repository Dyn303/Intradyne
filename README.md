# Intradyne-Lite: Shariah-Compliant Spot Crypto Paper Trader (Bitget)

Intradyne-Lite is a production-grade, event-driven, Shariah-compliant spot-only trading backend. It connects to Bitget via CCXT, runs in PAPER mode by default, enforces strict risk and compliance guardrails, and provides structured observability.

Key features:

- Paper trading by default; never sends live orders unless `MODE=live` and `LIVE_TRADING_ENABLED=true`.
- Venue: Bitget (via CCXT). Spot-only, long-only.
- Included strategies: momentum micro-scalper and mean-reversion micro-scalper (both long-only).
- Shariah compliance: whitelist-only symbols; blocks derivatives/margin/shorts; explainability ledger (append-only, hash-chained JSONL).
- Risk guardrails: SL/TP, position size caps, soft/hard drawdown halts, flash-crash shield, global kill-switch.
- Data: prefers WebSocket (if `ccxtpro` is available), falls back to REST polling. Asynchronous event pipeline.
- Observability: structured JSON logs, rotating file, FastAPI health/metrics endpoints, lightweight Prometheus-style metrics.

## Quick Start

1) Python 3.10+

2) Create a virtualenv and install:

```
python -m venv .venv
. .venv/bin/activate  # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

3) Configure environment:

```
cp .env.example .env
# Edit values as needed. By default MODE=paper and LIVE_TRADING_ENABLED=false
```

4) Run the app:

```
python -m app.main
```

5) Endpoints (default `PORT=8000`):

- `http://localhost:8000/readyz`
- `http://localhost:8000/healthz`
- `http://localhost:8000/metrics`
- `http://localhost:8000/state`

6) Run tests and linters:

```
pytest -q
```

Optionally:

```
ruff check . && ruff format .
mypy app
```

## Docker

Build and run:

```
docker build -t intradyne-lite -f docker/Dockerfile .
docker run --rm -p 8000:8000 --env-file .env intradyne-lite
```

## Safety & Compliance Notes

- Live trading requires `MODE=live` AND `LIVE_TRADING_ENABLED=true`; otherwise the system hard-fails on live order attempts.
- Only trades whitelisted spot pairs. No margin, futures, leverage, or shorting.
- Explainability ledger writes a JSONL entry with hash chaining per trade.

## Backtesting

This lightweight edition focuses on live paper trading against exchange data. For backtesting, hook your historical data reader into `data_ws.py` and drive the router with simulated ticks.

## Backtesting & Hyperparameter Tuning

- Backtest (historical replay, event-driven):

```
python -m app.backtest --symbols BTC/USDT,ETH/USDT --start 2024-01-01 --end 2024-03-01 --timeframe 1m --strategy momentum --params '{"momentum": {"breakout_window": 60, "min_range_bps": 5}, "risk": {"per_trade_sl_pct": 0.003, "tp_pct": 0.002}}'
```

- Optimize with Optuna (Hyperoptuna):

```
python -m app.optimize --symbols BTC/USDT,ETH/USDT --start 2024-01-01 --end 2024-03-01 --timeframe 1m --strategy momentum --trials 50 --jobs 2 --objective sharpe --lambda-dd 0.5
```

- Evaluate out-of-sample with saved best params:

```
python -m app.eval --symbols BTC/USDT,ETH/USDT --start 2024-03-02 --end 2024-04-01 --timeframe 1m --params-file artifacts/best_params.json
```

Notes:
- Backtests and optimization always use `PaperBroker`; no live endpoints are contacted.
- Shariah gates are enforced during backtest and optimization: whitelist-only symbols; spot-only; long-only; trials that violate gates are pruned/invalid.

## Run Paper Mode With Tuned Params

- Place a tuned params file at `artifacts/production_params.json` (sample provided). It may include both strategies under keys `momentum` and/or `meanrev`, plus optional `risk` overrides.
- Optionally point to a different file via env var `STRATEGY_PARAMS_FILE`.
- Start the app; it will load params and apply risk overrides at startup:

```
export STRATEGY_PARAMS_FILE=artifacts/production_params.json   # Windows: set STRATEGY_PARAMS_FILE=...
python -m app.main
```


## Project Layout

```
app/
  main.py            # bootstrap: config, tasks, server
  config.py          # Pydantic settings, whitelist integration
  whitelist.json     # Shariah-compliant spot pairs
  compliance.py      # whitelist checks, spot-only, no shorting
  risk.py            # SL/TP, sizer, drawdown, flash-crash, kill-switch
  ledger.py          # append-only explainability JSONL with hash chaining
  portfolio.py       # balances, positions, equity, P&L
  data_ws.py         # WebSocket (if available) or REST ticker polling
  broker_ccxt.py     # CCXT wrapper (spot-only); disabled for live unless enabled
  broker_paper.py    # Paper fills with fees, slippage, partials
  execution.py       # order manager routing to Paper or CCXT
  strategies/
    momentum.py      # momentum micro-scalper
    meanrev.py       # mean-reversion micro-scalper
  router.py          # strategy orchestration and concurrency
  metrics.py         # counters/gauges, EOD summary hooks
  server.py          # FastAPI with /readyz /healthz /metrics /state
tests/
  test_compliance.py
  test_risk.py
  test_paper_fills.py
```

## Definition of Done

- Tests green, lints clean, types pass; Docker image builds.
- No secrets in code, env-driven config.
- Paper mode by default; safe guards for live mode.
