# Operations Runbook

This runbook covers day‑2 ops: lifecycle, health, incidents, and backup/key rotation.

## Service Lifecycle (Docker + Compose)
- Dev stack: `make up` / `make down` (uses `deploy/docker-compose.yml`).
- Local image + run: `make build && make run` (binds examples, persists `./data`).
- Prod stack: `make prod-up` / `make prod-down` (loads `.env`, runs Caddy + API).
- Manual compose (example): `docker compose -f deploy/docker-compose.yml up -d --build`.
- Alt stack (local all-in-one):
  - Build and start: `docker compose -f @docker-compose.yml up -d --build`
  - Stop: `docker compose -f @docker-compose.yml down`
  - Logs: `docker compose -f @docker-compose.yml logs -f api`
  - Test inside container: `docker compose -f @docker-compose.yml run --rm api pytest -q @tests`

## Health, Logs, Metrics
- Liveness: `curl http://localhost:8000/healthz` or `make ping`.
- Connectivity check: `curl 'http://localhost:8000/ops/test_connectors?symbol=BTC/USDT' -H 'X-API-Key: dev'`.
- Logs (follow): `docker compose -f deploy/docker-compose.yml logs -f` (add `api` service name if desired).
- Metrics: app exposes latency stats via analytics endpoints; ship structured JSON logs to your stack (e.g., Loki/ELK). Ensure Docker JSON file driver or sidecar.
- OTEL tips: set `OTEL_EXPORTER_OTLP_ENDPOINT`, propagate `OTEL_SERVICE_NAME=intradyne-lite`, and run the Python process under an OTEL wrapper if you add instrumentation.

## Incidents & Playbooks
- Flash‑crash pause (>30% in 1h):
  1) Confirm with market data; 2) Validate `/signals/preview`; 3) Resume trading only after volatility subsides and risk lead approves.
- Kill‑switch (≥3 breaches/24h):
  1) Review recent breaches; 2) Fix root cause (config/connectors); 3) Clear halt via config/profile change and controlled restart; 4) Document in runbook.
- API outage:
  1) `make ping`; 2) `docker compose ... logs -f` for stack traces; 3) Check env/config mounts; 4) Roll back to last known good image (`docker images` → `docker run` tag pin).
- Connector failure:
  1) `/ops/test_connectors`; 2) Validate credentials and network egress; 3) Fail over to alternate account/profile if defined.

## Backup & Restore
- Data: backup `./data/trades.sqlite` (mapped to `/app/data`). Use `sqlite3 trades.sqlite ".backup 'trades-YYYYMMDD.sqlite'"` or copy with service stopped.
- Config: version `config.yaml`, `profiles.yaml`, `.env` in a secure secrets store; never commit real secrets.
- Restore: stop services, replace files, start stack, verify `/healthz` and analytics endpoints.

## Key/Secret Rotation
- Rotate `TG_BOT_TOKEN`, `TG_CHAT_ID`, `SMTP_*`, `MOBILE_SIGNING_KEY`, connector keys.
- Update `.env`, redeploy: `make prod-down && make prod-up`.
- Revoke old credentials at providers; verify alerts via `/ops/ping`.

References: see `@docs/RISK_GUARDRAILS.md`, `@docs/SHARIAH_FILTER.md`, `RUNBOOK.md`.
