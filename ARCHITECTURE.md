# IntraDyne Lite – Architecture Overview

All runtime Python lives in a single package: `src/intradyne/`. There is one
module per concern and no compatibility shims.

Structure
- `src/intradyne/api/*` – FastAPI app, routes, deps, rate limiting
- `src/intradyne/core/*` – config, logging, ledger, portfolio, types, AI helpers
- `src/intradyne/risk/*` – guardrails (pre-trade veto), drawdown, flash crash,
  kill switch, VaR, Shariah policy
- `src/intradyne/adapters/*` – venue adapters (Bitget via CCXT)
- `src/intradyne/backtester/*` – backtest engine and metrics
- `src/intradyne/data/*` – price feed and sentiment
- `src/intradyne/ml/*` – dataset/feature/label/model helpers
- `src/intradyne/strategies/*` – risk-profile allocation models (see note)
- `src/intradyne/sor/*` – smart order router
- `src/intradyne/engine/*` – the trading engine: strategies, execution,
  paper/live brokers, data feed, router
- `tests/` – pytest
- `deploy/` – compose, helm, monitoring
- `Dockerfile`, `docker/` – container build

Imports
- Always `intradyne.*`. CI and Docker set `PYTHONPATH=/app/src`, so
  `intradyne.*` resolves to `src/intradyne/*`.
- There is exactly one module per name. Previously the same code was reachable
  as `intradyne.X`, `src.intradyne.X` and `src.X` through two layers of
  re-export shims, which let the deployed app and the tested app drift apart.

Build & CI
- Lint: `ruff check src app tests`
- Types: `mypy` scoped via `mypy.ini` to `src/intradyne`
- Tests: `pytest`
- Docker: uvicorn serves `intradyne.api.app:app` on port 8000 (published as
  8080 on the host in compose)

Dev Tasks
- `make lint` / `make type` / `make test` – local quality gates
- `make docker-up` – build and start API (listens on `localhost:8080`)
- `make docker-logs` – follow container logs
- `make clean-artifacts` – remove generated backtest artifacts

Notes
- `src/intradyne/strategies/` holds risk-profile *allocation* models
  (conservative → very aggressive, producing portfolio weights). This is a
  different concern from `intradyne/engine/strategies/`, which holds
  tick-level entry signal generators. The four profile subclasses are currently empty and the
  subsystem is not wired into the API or the engine; whether to complete or
  drop it is an open product question.

See `MIGRATION.md` for the consolidation plan and its remaining phases.
