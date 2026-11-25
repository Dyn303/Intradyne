# IntraDyne — Operations Runbook

Covers the system as it actually exists. The previous version documented a
predecessor: connector setup for Alpaca and IBKR, and endpoints
(`/ops/ping`, `/ops/test_connectors`, `/profiles/apply`, `/watcher/stop`,
`/analytics/latency`) that this codebase has never had.

**Live trading is currently barred at startup.** See "Going live" below.

---

## 1. What runs

One container serves the API and, when enabled, hosts the trading loop in the
same process — one portfolio, one ledger, one pre-trade gate.

```
uvicorn intradyne.api.app:app        # the whole system
```

`ENGINE_ENABLED=true` starts the trading loop inside it. Off by default.

## 2. Start and stop

The supported Phase 3 deployment is `deploy/docker-compose.prod.yml` with
Caddy, PostgreSQL 16 and one API replica. It sets production authentication,
paper execution, and an idle strategy loop. Authenticated API paper orders
remain available. Helm and the Nginx variant are outside this rehearsal.

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

Set these in the deployment environment or the Compose interpolation file.
Keep secret values out of command history and Git.

| Variable | Purpose |
| --- | --- |
| `DOMAIN`, `CADDY_EMAIL` | Public hostname and ACME contact. Point DNS at Caddy before starting. |
| `X_API_KEY`, `ADMIN_SECRET` | Request authentication and distinct operator authorization. |
| `FRONTEND_ORIGINS` | Explicit comma-separated frontend origins, including scheme. |
| `POSTGRES_PASSWORD` | Database credential. If it contains URL-reserved characters, set `DB_URL` with a percent-encoded password. |
| `INTRADYNE_IMAGE` | Release image reference. Use an immutable registry digest with `up -d --no-build` for deployed releases and rollback. |

Engage the halt before shutting down. Use `docker compose -f deploy/docker-compose.prod.yml stop` to preserve the database and proxy volumes.

## 3. Health and state

| Endpoint          | Use                                                     |
| ----------------- | ------------------------------------------------------- |
| `/healthz`        | Process is up                                           |
| `/readyz`         | Database reachable. Observes only; never creates        |
| `/version`        | Running version                                         |
| `/metrics`        | Internal Prometheus scrape. Caddy returns 404.          |
| `/risk/status`    | Drawdown, VaR, breach count, halt state, thresholds     |
| `/portfolio`      | Balances and open positions                             |
| `/engine/status`  | Whether the loop is running, and on which symbols       |
| `/engine/state`   | Same portfolio the loop trades                          |
| `/ledger/tail`    | Recent explainability records                           |

Send your API key or valid Telegram Mini App auth to the API routes. The
static dashboard can load publicly. Operator mutations require the separate
secret. Use the command line to supply it.

## 4. Halting trading

The halt is the primary operator control. It is checked inside the pre-trade
gate, so it stops **every** order path — API-submitted and strategy-generated —
and is enforced again at the live broker boundary.

```bash
curl -X POST "https://$DOMAIN/admin/halt" \
  -H "X-API-Key: $X_API_KEY" -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

Confirm with `GET /risk/status` (`halted`, `halt_reason`). Every halt and every
refused order is written to the hash-chained ledger.

The operator halt is stored in `DB_URL` and survives process restart. Both
halt endpoints require `X-Admin-Secret` when `ADMIN_SECRET` is configured.
Production release requires that secret even when ordinary API or Telegram
authentication succeeds. Verify uncertain broker claims on the venue before
release. A halt cannot retract an order already sent to a venue.

For the paper stop and restore rehearsal, engage the halt, confirm a blocked
order, stop the service, and back up the database and ledger together. Restore
to a disposable environment with one API process and the engine disabled.
Confirm the persisted halt and the ledger verification result before release.
Keep one writer per ledger path and use a filesystem that supports file locks.
Never delete the lease file while a service may hold it. A corrupt ledger or
unavailable halt store at startup requires operator recovery before serving.

The Phase 1 rehearsal used two successive engine-disabled Docker containers.
The second opened a copy of the first container's stopped SQLite database and
ledger. It preserved the halt, rejected a paper order and a release without
the operator secret, accepted the authorized release, and verified the ledger.

### PostgreSQL recovery and rollback

Check `/readyz` through Caddy during and after an outage. Keep the halt set and
stop the API before backup so the database dump and JSONL ledger describe the
same stopped writer. Use `pg_dump -Fc` against the configured database, copy
the ledger, and record SHA-256 hashes for both. Keep the previous release image
digest with the backup.

Restore using `pg_restore --exit-on-error --no-owner` into a new database.
Restore the matching ledger to a new path, then set `DB_URL` and
`EXPLAIN_LEDGER_PATH` for one isolated API instance. Verify readiness, ledger
integrity, and the persisted halt through Caddy. Review outstanding order
claims and investigate any venue uncertainty before authorizing a release.
Paper balances are process-local and are reinitialized at restart. The backup
preserves the audited fills and durable risk and halt state.

For an image rollback, keep the API stopped while selecting the previous
`INTRADYNE_IMAGE` digest. If its database schema or ledger is incompatible,
point it at the matching verified restore. Resume through `up -d --no-build`,
repeat readiness and halt checks, and release only after operator review.

## 5. Alerts

Enable the `monitoring` profile with a file containing only the approved
Alertmanager webhook URL. Set `ALERT_WEBHOOK_FILE` to its path and
`GRAFANA_ADMIN_PASSWORD` to a separate secret. Grafana listens on loopback.
Prometheus scrapes the API on the Compose network and forwards alerts to the
internal Alertmanager. Confirm a halt alert arrives at the authorized test
receiver, record the receiver acknowledgement, then record who acknowledged
the alert and when. The rehearsal uses a disposable local receiver and makes
no external notification.

The checked-in n8n candidate workflow is inactive and runs by manual trigger.
Bind a Header Auth credential to `X-API-Key` to inspect an existing candidate.
Remove any older imported scheduled workflow that calls parameter apply. A
reviewed release image carries `production_params.json`. The candidate store
is separate, and operator authorization is required for apply and revert.

`deploy/monitoring/prometheus-alerts/intradyne.yml`, group `intradyne-safety`:

| Alert                       | Meaning                                          |
| --------------------------- | ------------------------------------------------ |
| `Trading_Halted`            | Kill switch or operator halt engaged             |
| `Unreconciled_Live_Orders`  | Submissions claimed but never completed — **verify on the venue** |
| `Drawdown_Approaching_Halt` | 30d drawdown reached `DD_WARN_PCT`               |
| `Drawdown_Halt_Threshold`   | Reached `DD_HALT_PCT`; the gate is halting        |
| `Guardrail_Breach_Rate`     | Breaches in 24h reached `KILL_SWITCH_BREACHES`   |
| `Live_Trading_Armed`        | Awareness: real funds are at risk                |

## 6. Verifying the audit trail

The ledger is append-only and hash-chained. To check it has not been altered:

```python
from intradyne.core.ledger import Ledger

ok, index, reason = Ledger(path="/app/data/explainability_ledger.jsonl").verify_chain()
```

`ok=False` names the first broken record. Investigate before trusting any
compliance claim based on it.

## 7. Exposure caps

Bound how much the system can transact, independent of the risk thresholds.
All in quote currency; `0` disables.

| Variable                   | Effect                                  |
| -------------------------- | --------------------------------------- |
| `MAX_ORDER_NOTIONAL`       | Largest single order                    |
| `MAX_SYMBOL_NOTIONAL_24H`  | Rolling 24h per symbol                  |
| `MAX_DAILY_NOTIONAL`       | Rolling 24h across all symbols          |

A cap that cannot be evaluated (an order with no price) is refused, not passed.

## 8. Going live — not yet enabled

Live trading is refused at startup by `assert_live_trading_gate()`. This is a
code gate (`LIVE_TRADING_GATE_OPEN` in `intradyne/core/config.py`), not an
environment variable, so opening it leaves a reviewable commit.

Built and tested:

- Triple gate: `MODE=live` **and** `LIVE_TRADING_ENABLED=true` **and** not halted
- Idempotency keys claimed before the venue is contacted and sent as
  `clientOrderId`, so a crash mid-submit cannot become a second real order
- Restart reconciliation: unresolved submissions halt trading rather than
  being guessed at in either direction
- Exposure caps (section 7)
- Safety alerting (section 5)

**Still required before opening the gate — none of it can be done from a
development machine:**

1. **Testnet soak.** Run against Bitget testnet with real keys for a
   sustained period. Confirm fills reconcile, the ledger chain verifies, and
   equity tracks the venue.
2. **Confirm alerts actually deliver.** The rules exist; that a page reaches a
   human has never been tested. Fire `Trading_Halted` deliberately and verify
   receipt.
3. **Set the exposure caps.** They default to `0` (disabled). Live trading
   without them means the only bound on transacted volume is the risk
   thresholds.

   `.env.example` now ships worked starting values with the derivation, for a
   nominal 10,000 equity at `MAX_POS_PCT=0.015`. Scale them linearly to your
   own equity. The point to carry across: **these cap turnover, not exposure**
   — twenty round trips a day at 1.5% transacts 60% of equity in daily notional
   while never holding more than 1.5% at once, so a daily cap set from
   position-size intuition halts the engine within the hour. The caps therefore
   sit *above* expected traffic; a cap that binds in normal operation is an
   outage, not a backstop, and repeated breaches trip `KILL_SWITCH_BREACHES`.

   Revise the turnover assumption after the soak, when it is measured rather
   than guessed. `tests/test_exposure_cap_defaults.py` pins the example against
   `MAX_POS_PCT` so the two cannot drift apart silently.
4. **Rehearse the halt** under live conditions and time it.
5. **Establish an edge after costs — the remaining blocker.** Every backtest
   summary now reports the measured win rate beside the breakeven it must
   clear, and `scripts/edge_report.py` scores runs. At the shipped defaults
   breakeven is ~88% (all-taker) or ~68% (all-maker). A run must land on
   `clears_with_margin`, not `marginal`.

   ```bash
   python scripts/edge_report.py --breakeven
   python scripts/edge_report.py --runs artifacts/backtests
   ```
6. ~~Obtain a Shariah ruling on the strategy itself.~~ **Settled.** A
   scholarly ruling has cleared high-frequency scalping as permissible for
   this system. The structural rules remain enforced in code — spot only,
   long only, no leverage, whitelist.

Record the gate evidence, responsible operator, scope, and rollback trigger.
After explicit operator sign-off, review the gate change in its own commit.
Use a separately authorized, small-scope rollout with configured caps, and
rehearse the rollback in section 4 before beginning it.

## 9. Common problems

| Symptom                              | Cause                                            |
| ------------------------------------ | ------------------------------------------------ |
| Container exits immediately          | A required variable is unset (section 2), or live trading is armed |
| Every request 401                    | Missing or wrong `X-API-Key`                     |
| Orders refused with `halt`           | Halt engaged; check `/risk/status` for the reason |
| Orders refused, `inventory unknown`  | A sell with no known position — long-only fails closed |
| `/engine/status` shows `running: false` with `enabled: true` | Loop crashed; supervisor restarts it, check logs |
| Loop logs `could not load ... markets` | Venue unreachable; falls back to the unfiltered whitelist |

## The engine refuses to start: "no edge has been demonstrated"

This is intentional, not a misconfiguration.

Measurement across 50 signals, 8 families, 943 days and two instruments puts
the best entry signal at roughly **0.5 bps** against a round-trip cost of
**4-14 bps**. The shipped strategy loses about 13 bps per round trip, so the
loop is gated shut at boot rather than quietly trading it. Full working in the
"Fifty signals" and "Months of data" sections of MIGRATION.md.

Three ways forward, depending on what you actually want:

**Run the API without the trading loop** — the normal choice for serving
endpoints, metrics and the ledger:

```bash
ENGINE_ENABLED=false
```

**Run the loop anyway, for research** — paper mode only. This is how a
replacement strategy gets validated, so it is deliberately permitted; it logs
a warning on every start:

```bash
ENGINE_ENABLED=true ACKNOWLEDGE_NO_EDGE=true MODE=paper
```

**Declare an edge** — flip `STRATEGY_EDGE_DEMONSTRATED` in
`src/intradyne/core/config.py`. Like the live-trading gate this is a code
change rather than an environment variable, so opening it leaves a reviewable
commit. Do not flip it without the measurement that justifies it: a win rate
on its own is not evidence, and `scripts/strategy_months.py` exists to produce
the walk-forward number that is.

Note that acknowledging the lack of edge does **not** open live trading.
`LIVE_TRADING_GATE_OPEN` is a separate, non-overridable check.
