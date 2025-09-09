
# IntraDyne UX Platform Blueprint (v1)
_Last updated: 2025-08-21 (Asia/Kuala_Lumpur)_

## 0) Objective
Elevate IntraDyne from CLI bot to a **fintech-grade platform** with a **web dashboard + mobile app**, delivering:
- Shariah certification readiness (governance & auditability)
- Institutional risk & execution features
- No-code operations for everyday users
- Scalable, observable backend

**Success metrics (first 90 days):**
- NPS ≥ 50, Daily Active Users ≥ 200, Retention D30 ≥ 35%
- Trade rejection by Shariah/risk correctly logged ≥ 99.9%
- P0 stability: API availability ≥ 99.95%, < 1 critical incident/month

---

## 1) Personas & Journeys
**P1. Retail Investor**: Connect broker → pick strategy → run paper/live → monitor PnL/compliance → export reports.  
**P2. Shariah Auditor**: View rules, overrides, evidence → sign off releases → export audit pack.  
**P3. Ops Manager**: Configure brokers/limits → set kill-switches → monitor incidents/alerts.  
**P4. Strategy Vendor**: Publish strategies to marketplace → versioning → revenue share.

**Golden paths:**
1. Onboard → KYC-lite → connect IBKR/Alpaca → run paper for 7 days → switch to live.  
2. Portfolio breach → auto kill-switch → alerts → post-mortem report generated.  
3. Annual Zakat & purification report export (PDF + CSV).

---

## 2) High-Level Architecture
**Clients**
- Web: React + Next.js (SSR), mobile: React Native.
- Auth via OpenID Connect (Keycloak/Auth0/Cognito).

**Edge**
- API Gateway (NGINX/Envoy) → FastAPI services.
- WebSocket gateway for live updates.

**Core Services (FastAPI, Python 3.11)**
- **Trade Orchestrator**: signal intake, state machine, idempotent order flow.
- **Shariah Compliance Service**: sector & ratio screens, fatwa rules, override ledger.
- **Risk Engine**: pre-trade checks (exposure, VaR/ES), kill-switch.
- **Broker Router**: adapters (Paper, IBKR, Alpaca, Tradier); order status fanout.
- **Portfolio Service**: holdings, PnL, performance, benchmarks.
- **Market Data Ingest**: quotes, fundamentals; write to time-series.
- **Strategy Service**: strategy marketplace, sandbox runs, backtests.
- **Reporting Service**: Zakat/purification, compliance packs, incident reports.
- **Notification Service**: email/push/SMS.
- **Config/Secrets**: user & org configs; Vault for secrets.

**Data Layer**
- **PostgreSQL** (transactional), **TimescaleDB** (PnL/quotes), **ClickHouse** (analytics), **S3-compatible storage** (reports/artifacts).
- **Redis/RabbitMQ** for queueing & caching.

**Observability**
- OpenTelemetry → Prometheus (metrics), Loki (logs), Tempo (traces), Grafana (dashboards).

**Security**
- mTLS between services, per-tenant encryption keys, WAF, rate-limiting, IDS/IPS hooks.

---

## 3) Domain Flow (Happy Path)
1. **Strategy signal** created →
2. **Shariah screen** (sector & ratios) → **PASS/FAIL** with evidence →
3. **Risk** (exposure, VaR/ES, daily budget) → **PASS/FAIL** →
4. **Execution** via Broker Router →
5. **Audit** record (immutable) →
6. **Portfolio** & **Notifications** update UI in real time.

**Failure modes**: any FAIL triggers alerts + optional kill-switch and an incident ticket.

---

## 4) UI/UX Information Architecture
### Web Dashboard (primary views)
1. **Home / Overview**: Equity curve, Open PnL/Closed PnL, Compliance status (green/amber/red), Risk budget meter.
2. **Orders & Positions**: Live blotter, fills, latency, slippage vs benchmark (VWAP/TWAP).
3. **Compliance**: Rulebook viewer, symbol verdicts, overrides with reason & approver, audit timeline.
4. **Risk**: Exposure by asset/class, VaR/ES, stress tests, drawdown waterfall, kill-switch.
5. **Strategies**: Marketplace (cards), install/activate, sandbox backtests, versioning & changelog.
6. **Reports**: Zakat/purification, annual summary, incident post-mortems, export (PDF/CSV).
7. **Settings**: Broker connections, API keys, feature flags, notification prefs, webhooks.

### Mobile App (RN)
- **Today**: PnL snapshot, alerts, compliance status.
- **Orders**: Approve/hold/cancel (RBAC).
- **Kill-switch**: Tap-to-halt (with 2FA + confirm).
- **Notifications**: Trade fills, breaches, approvals.

---

## 5) APIs (representative)
- `POST /v1/strategies/{id}/signals` – submit signals (body: symbol, side, qty, ts, meta).
- `POST /v1/orders` – create order (idempotency-key, client_order_id).
- `GET /v1/compliance/verdicts?symbol=` – latest verdict + evidence.
- `POST /v1/risk/check` – dry-run risk decision for hypothetical order.
- `GET /v1/portfolio/overview` – positions, PnL, exposures.
- `POST /v1/reports/zakat` – generate report (async job → URL).
- `POST /v1/admin/kill-switch` – toggle; reason required; audited.
- `POST /v1/admin/brokers/connect` – OAuth/API-key exchange into Vault.

**Standards**: OAuth2/OIDC, JWT with short TTL; request signing for broker callbacks; idempotency headers; pagination & filtering; webhooks.

---

## 6) Data Model (key tables)
- `users(id, tenant_id, rbac_role, mfa_enabled, ...)`
- `brokers(id, tenant_id, type, status, vault_ref, ...)`
- `strategies(id, vendor_id, version, risk_profile, status, ...)`
- `signals(id, strategy_id, symbol, side, qty, ts, meta)`
- `orders(id, client_order_id, state, symbol, side, qty, price, broker_id, ts_created, ts_filled, ...)`
- `positions(id, symbol, qty, avg_price, mtm, ...)`
- `compliance_verdicts(id, symbol, ruleset_ver, verdict, evidence_json, approver_id, ts)`
- `risk_checks(id, order_id, result, limits_hit_json, ts)`
- `audits(id, entity_type, entity_id, action, actor, before, after, ts)`
- `reports(id, type, status, s3_uri, ts)`

**Event bus topics**: `signals.new`, `orders.submitted`, `orders.filled`, `compliance.fail`, `risk.fail`, `killswitch.on`, `reports.ready`.

---

## 7) Shariah & Risk Details
**Compliance engine**
- Sector blacklist, ratio thresholds (AAOIFI-aligned), per-tenant overrides.
- Evidence bundle: source, calc, timestamp, reviewer; immutable log + hash chain.
- Workflow: Draft → Review → Approved (RBAC).

**Risk engine**
- Exposure limits, concentration, leverage, cash floor.
- **VaR/ES** (historical & parametric), **stress scenarios**, **intraday budget**.
- **Kill-switch**: auto (rule-based) & manual (RBAC + 2FA).

---

## 8) Security & Governance
- 2FA, device binding; session anomaly detection.
- Per-tenant encryption keys; broker secrets in Vault; rotation policies.
- WAF + rate limiting; IP allowlist for admin endpoints.
- **Compliance**: audit trails, tamper-evident logs, privacy controls (GDPR-like).

---

## 9) Observability & SLOs
- **SLOs**: API 99.95% / Websocket 99.9% / P95 order roundtrip < 350ms (paper), < 700ms (live).
- **Dashboards**: PnL latency, fill ratio, risk breaches, compliance overrides, alert fatigue.
- **Runbooks**: incident response, kill-switch procedures, data backfill, broker failover.

---

## 10) No‑Code Operations
- Strategy Marketplace: install/enable/configure via UI forms; YAML hidden.
- Rulebook Editor: sliders & toggles for thresholds, with preview & “what-if” tests.
- Backtesting UI: upload CSV or select data range; sandbox run; compare variants; publish.
- Reporting Wizard: select period → generate → share link → revoke access.

---

## 11) Delivery Plan (90 days)
**Phase 1 (Weeks 1–4)**: Auth & tenants, Portfolio + Orders, Paper broker, Compliance v1, Risk v1, Overview UI, Notifications.  
**Phase 2 (Weeks 5–8)**: IBKR adapter, Strategy marketplace (basic), Backtesting UI, Reports v1, Mobile MVP (alerts + kill-switch).  
**Phase 3 (Weeks 9–12)**: VaR/ES + stress tests, VWAP/TWAP, Multi-broker, Auditor workspace, Certifications prep.

**Exit criteria (→ 9.5/10):**
- Auditor can review & sign compliance pack end-to-end.
- Mobile kill-switch with 2FA working.
- Portfolio & risk dashboards real-time (≤ 2s freshness).
- Reports downloadable; all actions audited; zero P0 bugs in final 2 weeks.

---

## 12) Tech Stack (recommended)
- **Frontend**: React/Next.js + RN, Zustand/Redux, WebSocket.
- **Backend**: FastAPI, Pydantic, Celery/RQ, Redis/RabbitMQ.
- **Data**: Postgres + TimescaleDB, ClickHouse, S3.
- **Auth**: Keycloak/Auth0/Cognito.
- **SecOps**: Vault, OPA for policy, Falco/Suricata.
- **CI/CD**: GitHub Actions, Infra as Code (Terraform), Helm/K8s or ECS.
