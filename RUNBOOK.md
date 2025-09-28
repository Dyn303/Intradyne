
# IntraDyne Lite — RUNBOOK (v1.9.0-final)

## 1. Build & Local Run
- `make build`
- `make run` (serves API at http://localhost:8080; uses example configs)
- Slim API image: `make build-api && make run-api` (no heavy deps; ~216MB)

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
- Guardrails (API): drawdown, flash-crash, kill-switch, VaR step-down.
- Admin kill-switch: `POST /admin/halt {"enabled": true}` — now enforced in order gating.

## 5. Alerts
- Telegram (`TG_BOT_TOKEN`, `TG_CHAT_ID`) and/or SMTP envs.
- Heartbeat: `GET /ops/ping`
- Sentiment gauge: `GET /data/sentiment` returns normalized score [-1,1] and updates Prom metric `intradyne_sentiment_score`.

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
- Rate limits: enable Redis in prod (`REDIS_URL`) for resilient fixed-window counting. HTTP routes have general RL; WS streams use token-bucket.

## 11. Dev QA
- Lint: `.venv/Scripts/python -m ruff check intradyne src app tests`
- Typecheck: `.venv/Scripts/python -m mypy` (scoped via `mypy.ini` to core modules)
- Tests: `.venv/Scripts/python -m pytest -q`

## 12. Auth & Security
- API auth: default ON in production (set by `APP_ENV`); or force with `API_AUTH_REQUIRED=1`.
- Admin endpoints: require `ADMIN_SECRET` header (`X-Admin-Secret`) when set.
- Do not commit secrets; use environment or secret store in production.

## 13. Data & Backtests
- Local OHLC CSVs: place under `data/<exchange>/<SYM>_<tf>.csv` (e.g., `data/bitget/BTC-USDT_1h.csv`).
- Supported TFs include `1m`, `15m`, `1h`, `4h`, `1d`.
- Backtest (30 days example):
  - PowerShell:
    - `$start=(Get-Date).AddDays(-30).ToString('yyyy-MM-dd')`
    - `$end=(Get-Date).ToString('yyyy-MM-dd')`
    - `$json='{"risk":{"use_atr":true,"atr_window":14,"atr_k_sl":1.5,"atr_k_tp":2.5,"max_pos_pct":0.01,"per_trade_sl_pct":0.003,"tp_pct":0.003,"dd_soft":0.02,"dd_hard":0.04},"filters":{"ema_fast":50,"ema_slow":200,"min_atr_pct":0.0008,"max_atr_pct":0.02},"execution":{"micro_slices":2,"time_stop_s":3600,"trail_atr_k":0.5}}'`
    - `.venv\Scripts\python -m app.backtest --symbols "BTC/USDT,ETH/USDT" --start $start --end $end --timeframe 1h --strategy momentum --params $json`

## 14. Parameter Sweeps
- Grid search helper: `scripts/sweep_backtests.py`
- Example:
  - `.venv\Scripts\python scripts\sweep_backtests.py --symbols BTC/USDT,ETH/USDT --timeframe 1h --ema-fast "20,50,100" --ema-slow "50,200" --tp "2.5,3.0" --atr-min "0.0010,0.0015,0.0020" --sent-min "0.0,0.1,0.2"`
- Outputs CSV/JSON under `artifacts/reports/` and prints top configs meeting guardrails (win≥65%, DD≤20%, daily≥1%).

## 2.1 Production with TLS (Updated)
- Create and fill `.env` with `CADDY_EMAIL`, `DOMAIN`, broker creds, and `LOG_LEVEL`.
- Start: `docker compose -f deploy/docker-compose.prod.yml up -d --build`
- Verify: `https://$DOMAIN/healthz`, `https://$DOMAIN/readyz`, `https://$DOMAIN/metrics`
- Resources: default caps ~512MB RAM and 0.5 CPU (edit `deploy/docker-compose.prod.yml`).
- Network: Caddy (ports 80/443) reverse-proxies to `api:8000` inside the network and manages TLS.

### Email alerts (Grafana SMTP)
- Set SMTP in `.env` (examples):
  - `GF_SMTP_ENABLED=true`
  - `GF_SMTP_HOST=smtp.example.com:587`
  - `GF_SMTP_USER=your_user`
  - `GF_SMTP_PASSWORD=your_pass`
  - `GF_SMTP_FROM_ADDRESS=alerts@example.com`
  - `GF_SMTP_FROM_NAME=Intradyne Grafana`
- Restart monitoring: `make monitoring-down && make monitoring-up`
- Update receiver email in `deploy/monitoring/grafana/provisioning/alerting/contact-points.yaml` or via Grafana UI.

## 2.2 Nginx variant (edge rate limiting)
- Start edge proxy: `docker compose -f deploy/docker-compose.nginx.yml up -d --build`
- Optional monitoring: `docker compose -f deploy/docker-compose.nginx.yml --profile monitoring up -d --build`
- Nginx exposes `/nginx_status` internally; exporter publishes metrics to Prometheus.
- Alerts: Prometheus rule `Nginx_5xx_Rate` warns on elevated 5xx.
