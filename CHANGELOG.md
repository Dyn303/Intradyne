# IntraDyne Lite Changelog

## v1.7.2 â€” 2025-09-04
- Heavy LTS repack: bundles prior Lite builds and all fullstack archives under `/_prev/`.
- Carries: v1.7.1 (base), v1.6 (Lite), fullstack v1.3â€“v1.9, and UX Blueprint.

## v1.7.3 â€” 2025-09-04
- Master bundle: added fullstack v1.3â€“v1.9 archives and UX blueprint under `/_prev/external/`.

## v1.7.4 â€” 2025-09-04
- Wired _daily_pnl to SQLite DB; added ops test & alerting (Telegram/SMTP).

## v1.7.5 â€” 2025-09-04
- Real connectors: CCXT/Alpaca/IBKR, account router, open/cancel endpoints.

## v1.7.6 â€” 2025-09-04
- CCXT virtual bracket watcher; IBKR OCA brackets; strategy profiles and /profiles/apply.

## v1.7.7 â€” 2025-09-04
- Analytics PNG/JSON, options templates, mobile endpoints; embedded all external archives.

## v1.7.8 â€” 2025-09-04
- Options placement (IBKR), CSV analytics, mobile OpenAPI, all external files embedded.

## v1.7.9 â€” 2025-09-04
- IBKR option OCA exits; PnL grouping; latency stats; signed URL feeds for mobile.

## v1.8.0 â€” 2025-09-04
- Dashboards per profile/account; healthz; autostart watcher/profile; docker-compose + Helm chart.

## v1.9.0-final â€” 2025-09-04
- Finalized Dockerized backend: hardened Dockerfile, prod compose with Caddy TLS, rate limiting middleware, env templates, Makefile, runbook & security notes.

## v1.9.1 â€” 2025-09-10
- Added `/ops/test_connectivity` endpoint for internal/external probes.
- Compose: healthcheck now uses `/readyz`; removed obsolete top-level `version:`.
- Fix: ensure `PYTHONPATH=/app/src` and uvicorn target `intradyne.api.app:app` so API boots under Compose.

## v1.9.2 - 2025-09-11
- Fix: syntax error in `app/main.py` f-string (blocked typecheck).
- Chore: ruff auto-fixes across repo; added targeted `ruff: noqa` for compatibility re-export shims under `src/intradyne/*`.
- Dev: added `mypy.ini` to scope type-checking to `src/engine.py` and `src/backtester/` (core typed surface), excluding legacy/shim modules.
- Tests: installed `httpx` for FastAPI TestClient; all tests pass.
- Docker: no image changes; compose build remains green.

## v1.9.3 - 2025-09-11
- Infra: container now serves API by default (uvicorn), healthchecked via `/readyz`, port published `8080->8000` in compose.
- Hygiene: added `.dockerignore` to trim build context; tightened `.gitignore` for artifacts/data/venv.
- Core: added `src/intradyne/core/logging.py` with `setup_logging` + JSON logs; maintains secret redaction.
- Status: lint clean, tests green, mypy (scoped) passes, container healthy.

## v1.9.4 - 2025-09-28
- API: add general rate limiter for non-AI routes (Redis or in-memory sliding window).
- API: initialize structured JSON logging on startup for consistent logs.
- Core: clean allowed symbol builder to avoid self-pairs (e.g., USDT/USDT).
- Core: deduplicate logging implementation; `src/intradyne/core/logging.py` now re-exports canonical `src/core/logging.py`.
- CI: ensure ruff formatting applied repo-wide.
