
# IntraDyne Lite — RUNBOOK (v1.9.0-final)

## 1. Build & Local Run
- `make build`
- `make run` (serves API at http://localhost:8080; uses example configs)

## 2. Production with TLS
1) Copy `.env.example` → `.env` and fill secrets (`MOBILE_SIGNING_KEY`, `CADDY_EMAIL`, `DOMAIN`, broker keys).
2) `make prod-up`
3) Check: `curl -k https://$DOMAIN/healthz`
4) Metrics: `GET /metrics` (Prometheus format)

## 3. Accounts & Connectivity
- Configure `accounts[]` in `config.yaml` (CCXT/Alpaca/IBKR).
- Smoke test: `GET /ops/test_connectors?symbol=BTC/USDT`

## 4. Risk Controls
- Update `risk.capital`, `daily_max_loss_pct` (via profile).
- PnL guard uses SQLite `trades` table (`storage.sqlite_path`).

## 5. Alerts
- Telegram (`TG_BOT_TOKEN`, `TG_CHAT_ID`) and/or SMTP envs.
- Heartbeat: `GET /ops/ping`

## 6. Watcher & Profiles
- Autostart via env: `WATCHER_AUTOSTART=1`, `PROFILE_DEFAULT=hybrid`
- Manual apply: `POST /profiles/apply?name=scalper|swing|hybrid`

## 7. Options (IBKR)
- Place covered call / protective put; set OCA exits.
- Ensure TWS/IBG API is up.

## 8. Backups
- Persist `/app/data` volume (SQLite). Snapshot nightly.

## 9. Rollback
- Every release bundles previous under `/_prev/`. Replace image tag and `config.yaml` if needed.

## 10. Incident
- Check logs (`docker logs`), `/analytics/latency`, and alert channel.
- Toggle profile or stop watcher: `/watcher/stop`
 - Health: `GET /healthz`, Readiness: `GET /readyz`, Metrics: `GET /metrics`

## 11. Dev QA
- Lint: `.venv/Scripts/python -m ruff check intradyne src app tests`
- Typecheck: `.venv/Scripts/python -m mypy` (scoped via `mypy.ini` to core modules)
- Tests: `.venv/Scripts/python -m pytest -q`
