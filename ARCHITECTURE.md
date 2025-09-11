# IntraDyne Lite – Architecture Overview

This repo follows a src-first package layout, with canonical runtime modules under `src/`. The legacy top-level `intradyne/` package is retained as a thin compatibility layer so existing imports keep working while we migrate.

Structure
- `src/`: canonical Python packages used in runtime and Docker images
  - `src/intradyne/api/*`: FastAPI app, routes, and deps
  - `src/intradyne/core/*`: core config, logging, types, adapters
  - `src/backtester/*`, `src/engine.py`: backtesting/engine utilities
- `intradyne/`: compatibility shims for stable imports
  - Re-exports from `src/intradyne/*` or provide minimal stubs needed by tests
- `app/`: standalone demo/backtest pipeline (kept for examples/tests)
- `tests/`: unit tests (pytest)
- `deploy/`: docker-compose files
- `docker`, `Dockerfile`: container build

Imports
- Prefer `intradyne.*` imports at app level. CI and Docker set `PYTHONPATH=/app/src`, so `intradyne.*` resolves to `src/intradyne/*`.
- Shims in `intradyne/*` ensure backward compatibility; they can be retired once all callers are updated to the canonical modules.

Build & CI
- Lint: ruff on `intradyne src app tests`
- Types: mypy scoped via `mypy.ini` (starts with `src/engine.py` and `src/backtester/`)
- Tests: pytest
- Docker: uvicorn serves `intradyne.api.app:app` on port 8000 inside the container (published as 8080 on host in compose)

Dev Tasks
- `make lint` / `make type` / `make test` – local quality gates
- `make docker-up` – build and start API (listens on `localhost:8080`)
- `make docker-logs` – follow container logs
- `make clean-artifacts` – remove generated backtest artifacts

Migration Plan
1) Stabilize shims (done) – all tests green with re-exports or minimal stubs
2) Expand typing coverage module-by-module
3) Replace wildcard re-exports with explicit re-exports
4) Retire legacy dirs (`@src`, `@tests`, `intradyne_lite`) under `_prev/legacy/` (kept ignored)

