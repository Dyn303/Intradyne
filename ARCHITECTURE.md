# IntraDyne Lite – Architecture Overview

All runtime Python lives in a single package: `src/intradyne/`. There is one
module per concern and no compatibility shims.

Structure
- `src/intradyne/api/*` – FastAPI app, routes, deps, rate limiting
- `src/intradyne/core/*` – config, logging, ledger, portfolio, types, AI helpers,
  and `db.py`, the SQLite/Postgres seam (see Storage below)
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

Storage
- Three durable stores share one database: `core/equity.py` (equity history,
  read by the drawdown and VaR guardrails), `core/idempotency.py` (order keys,
  claimed before a live order is sent) and `core/limits.py` (traded notional,
  for the exposure caps). All three are durable for the same reason: a
  guardrail that re-arms from zero on restart is not a guardrail.
- `DB_URL` chooses the engine. `sqlite:///...` is the default in `config.py`
  and what the test suite runs against; `postgresql://...` is what the compose
  stack uses, because SQLite in WAL mode cannot be written through a Docker
  Desktop bind mount. `core/db.py` holds the difference — connection handling,
  placeholder style, the three places the dialects genuinely diverge — and the
  store classes are unchanged between backends. An unrecognised scheme is
  refused rather than defaulted.
- Switching is one environment variable in both directions. Copy the data
  first with `scripts/migrate_sqlite_to_postgres.py` (`make db-migrate`);
  starting against an empty equity table means `dd_30d([]) == 0.0` and a
  drawdown halt re-armed from zero. The script never writes to the SQLite
  side, so that file remains the rollback.
- The explainability ledger is deliberately *not* in either database. It is an
  append-only hash chain in a JSONL file and is single-writer by construction;
  two processes appending would fork the chain.
- The Postgres half of `tests/test_db_backends.py` skips without a live
  database. Until `TEST_POSTGRES_URL` is set (`make test-postgres`), CI covers
  the SQLite path and the dialect logic, not the Postgres path itself.

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

See `MIGRATION.md` for the consolidation plan and its remaining phases, and
`docs/STRATEGY_RESEARCH_FRAMEWORK.md` for the rules any strategy research must
satisfy before it runs (with `docs/PREREGISTRATION_TEMPLATE.md` alongside it).
