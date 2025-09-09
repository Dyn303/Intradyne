
# IntraDyne Lite

## Overview
IntraDyne Lite is a minimal, deploy-ready backend for research and execution of spot-only, Shariah-aware crypto trading. It exposes a FastAPI service with guardrails, analytics, profile presets, and connectors (CCXT/Alpaca/IBKR) bundled for paper/live integration.

## Features
- Guardrails (defaults on): daily PnL throttle, rate-limiting, profile-based risk, and alerting. Roadmap guardrails include 30â€‘day drawdown checks, flashâ€‘crash pause, killâ€‘switch, VaR step-down, whitelist-only symbols, and an explainability ledger.
- Shariah filter: `/shariah/check` blocks non-compliant symbols before trade routing.
- Research endpoints: signals preview, sentiment gate, ATR-based sizing.
- Options (IBKR), analytics (CSV/PNGs), mobile-friendly endpoints.

## Architecture (summary)
- API: `intradyne_lite/api/server.py` (FastAPI + rate limit middleware)
- Core: `intradyne_lite/core/*` (connectors, profiles, analytics, watcher, options)
- Config: `config.yaml` and `profiles.yaml` (mounted or packaged); env overrides
- Storage: SQLite at `/app/data/trades.sqlite` (mounted as `./data` in Docker)
- Deploy: `deploy/docker-compose.yml`, Helm chart at `deploy/helm/intradyne-lite`

## Prerequisites
- Windows 10/11 with Admin rights
- Python 3.11+
- Node.js LTS (for tooling/clients if used)
- Docker Desktop (with WSL2 backend on Windows)

## Quickstart
Windows (native):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# copy env template and edit keys
Copy-Item @.env.example .env
# optionally point pydantic loader to .env
$env:ENV_FILE=".env"
uvicorn intradyne_lite.api.server:app --reload --port 8000
```
Windows with WSL2 + Docker:
```bash
make up     # builds and starts via deploy/docker-compose.yml
make down   # stop stack
```
Docker only:
```bash
make build && make run   # local image + container
```

Configure environment
- Edit `.env` using the fields in `.env.example` (CCXT, Alpaca, IBKR, DB_URL, REDIS_URL, risk thresholds, logging).
- The new `src/config.py` exposes `load_settings()` (pydantic) that reads env and optional `ENV_FILE`.

## .env sample (prod)
```
# API and rate limits
RATE_LIMIT_WINDOW=60
RATE_LIMIT_REQS=120

# Connectors (set only what you use)
ALPACA_KEY=xxx
ALPACA_SECRET=xxx
CCXT_EXCHANGE_ID=binance
CCXT_APIKEY=xxx
CCXT_SECRET=xxx
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=7

# Notifications / mobile signing
TG_BOT_TOKEN=xxx
TG_CHAT_ID=123456
SMTP_HOST=
SMTP_USER=
SMTP_PASS=
MOBILE_SIGNING_KEY=change-me

# App
CONFIG=/app/config.yaml
PROFILE_DEFAULT=scalper
WATCHER_AUTOSTART=true
```

## Run (dev and prod)
- Dev (local Python): `uvicorn intradyne_lite.api.server:app --reload --port 8000`
- Dev (Docker): `make run` (binds `config.yaml.example`, `profiles.yaml.example`, persists `./data`)
- Prod (compose + .env): `make prod-up` / `make prod-down`
- Health: `make ping` or `curl http://localhost:8000/healthz`

## Tests
- No formal suite included. Add focused tests under `tests/` as `test_*.py`.
- Example run: `pytest -q`
- Manual checks: `/ops/test_connectors`, `/healthz`, `/signals/preview`.

## Troubleshooting
- OneDrive paths: long or synced paths can break Docker/WSL bind mounts. Prefer a short path outside OneDrive (e.g., `C:\dev\intradyne-lite`).
- Long file names: enable Git long paths on Windows:
  `git config --global core.longpaths true`
- Ports in use: change `--port` for uvicorn or adjust compose mapping.
- SSL cert errors (Docker on corp networks): ensure Docker Desktop trust store and proxy settings are configured.

## FAQ
- Q: Does it trade derivatives? A: No, spot-only by design.
- Q: How to authenticate? A: Send `X-API-Key` with a non-empty value unless hardened behind a gateway.
- Q: Where to configure compliance filters? A: `config.yaml` and env; call `/shariah/check` to validate symbols.
- Q: Where is data stored? A: SQLite at `./data` (mapped to `/app/data`).
- Q: How do I change rate limits? A: Set `RATE_LIMIT_WINDOW` and `RATE_LIMIT_REQS` in env.

Endpoints:
- POST /shariah/check?symbol=
- POST /sentiment/set?symbol=&score=
- GET  /sentiment/get?symbol=
- POST /strategy/toggle (JSON body with toggles)
- GET  /signals/preview?symbol=&timeframe=1h&ma_n=50
- GET  /strategy/suggest_qty?symbol=&risk_pct=0.01

Notes:
- This is a minimal skeleton to demonstrate research features. Integrate order routes and DB to enforce daily max loss and Shariah checks pre-trade.


## v1.7.4 â€” Live Readiness Upgrades
- **Daily PnL guard wired** to SQLite at `storage.sqlite_path` (default `/app/data/trades.sqlite`).
- **Ops endpoints**:
  - `GET /ops/ping` â†’ sends Telegram/Email alert (env: TG_BOT_TOKEN, TG_CHAT_ID, SMTP_*).
  - `GET /ops/test_connectors?symbol=BTC/USDT` â†’ quick connectivity+market data test.
  - `POST /ops/test_trade` â†’ dry-run micro trade with all guards (set `dry_run=false` to attempt live integration).
- **Alerts** fire on: heartbeat ping, Shariah/sentiment blocks, daily-loss throttle.


## v1.7.5 â€” Live Connectors (CCXT / Alpaca / IBKR)
### Config examples (`config.yaml`)
```yaml
risk: { capital: 10000 }
storage: { sqlite_path: /app/data/trades.sqlite }
accounts:
  - id: ccxt-binance
    kind: ccxt
    exchange_id: binance
    apiKey: "YOUR_KEY"
    secret: "YOUR_SECRET"
    sandbox: true
    params: {}
  - id: alpaca-paper
    kind: alpaca
    key: "YOUR_KEY"
    secret: "YOUR_SECRET"
    base_url: "https://paper-api.alpaca.markets"
  - id: ibkr-paper
    kind: ibkr
    host: "127.0.0.1"
    port: 7497     # TWS paper
    clientId: 7
```

### New endpoints
- `GET  /orders/open?account=...&symbol=`
- `POST /orders/cancel?order_id=...&account=...&symbol=`

> Existing order routes in your app now use the real connector behind `_choose_conn()`. Bracket support is native on Alpaca; CCXT bracket is best-effort (TP limit + attempt SL). IBKR live bracket can be managed from TWS or extended via ib_insync.


## v1.7.6 â€” CCXT Virtual Brackets + IBKR OCA + Profiles
- **Virtual Bracket Watcher (for CCXT venues lacking native SL/TP):**
  - `POST /watcher/start` / `POST /watcher/stop`
  - `POST /watcher/register` with `{account,symbol,side,qty,tp,sl}`
  - Stores records in SQLite table `virtual_brackets` and monitors prices to close positions when TP/SL hit.
- **IBKR Brackets (OCA)** via `ib_insync`: parent order + TP (limit) + SL (stop) are linked via OCA group.
- **Profiles**: `POST /profiles/apply?name=scalper|swing|hybrid` or define `/app/profiles.yaml` to override defaults.

Example `profiles.yaml` override:
```yaml
scalper:
  atr_mult: 1.2
  risk_per_trade_pct: 0.004
hybrid:
  sentiment_gate: true
  min_sentiment: 0.1
```


## v1.7.7 â€” "Add All" (Analytics + Options + Mobile API + Full History)
- **Analytics**: `/analytics/summary`, `/analytics/trades?limit=`, `/analytics/equity.png?days=`
- **Options templates** (Shariah-allowed): `/options/covered_call`, `/options/protective_put` (build-only, not placing)
- **Mobile API**: `/mobile/summary`, `/mobile/trades/recent`
- **History packaged**: all external archives nested under `/_prev/external/`


## v1.7.8 â€” Options Placement (IBKR) + CSV Exports + Mobile OpenAPI
- **CSV exports**: `/analytics/trades.csv`, `/analytics/equity.csv`
- **IBKR Options** (placement for allowed templates):
  - `POST /options/place/covered_call` (fields: account, symbol, qty, strike, expiry 'YYYYMMDD')
  - `POST /options/place/protective_put` (fields as above)
- **Mobile OpenAPI**: `public/mobile-openapi-v1.7.8.yaml` for your mobile app team.


## v1.7.9 â€” Options OCA Exits + PnL Grouping + Latency + Signed URLs
- **Options exits (IBKR OCA)**: `POST /options/exits/oca` with `opt_type=CALL|PUT`, `side=long|short`, `contracts`, `strike`, `expiry`, `tp_price`, `sl_price`.
- **Trade logging**: `POST /trades/log` adds metadata (`strategy`, `profile`, `venue`).
- **PnL by group**: `GET /analytics/pnl_by?group=account|strategy|profile|venue`.
- **Latency**: auto-logged for key ops; `GET /analytics/latency?group=action|account&days=7`.
- **Mobile signed URLs**: `GET /mobile/signed_urls` â†’ `/signed/equity.png` & `/signed/trades.csv` (requires `MOBILE_SIGNING_KEY` env).


## v1.8.0 â€” Deploy-Ready + Dashboards + Autostart
- **Dashboards**:
  - Profile/account summaries: `/analytics/profile/summary`, `/analytics/account/summary`
  - Equity PNGs: `/analytics/profile/equity.png`, `/analytics/account/equity.png`
- **Ops**:
  - `/healthz` endpoint, **WATCHER_AUTOSTART** and **PROFILE_DEFAULT** envs on startup.
- **Deployment tooling**:
  - `deploy/docker-compose.yml` for quick single-node runs.
  - **Helm chart** at `deploy/helm/intradyne-lite` (ConfigMap+PVC+Secret).


