# IntraDyne — Consolidation Plan

**Target:** one service, one image, one config, one ledger. Live-capable by
construction, paper-only and hard-gated in this phase.

**Status:** Phase 0 complete. Phase 1 next.

---

## Why

The repo currently contains two unrelated systems:

- `app/` — the real trading engine: momentum/mean-reversion strategies, CCXT
  Bitget adapter, paper broker, stateful risk manager. **Never deployed.**
- `src/` — the FastAPI service: guardrails, explainability ledger, admin
  endpoints. **This is what ships**, and its `POST /orders` returns a fabricated
  UUID without touching a broker.

Every document in the repo describes one or the other, never both, and
`RUNBOOK.md` documents a third system that no longer exists (the archived
`_prev/legacy/intradyne_lite/`). The plan below collapses this to a single
service.

---

## The two-tier risk model

`Guardrails` (in `src/risk/guardrails.py`) and `RiskManager` (in `app/risk.py`)
read as duplicates but are not — they do different jobs, and each is missing
what the other has:

|          | `Guardrails`                          | `RiskManager`                              |
| -------- | ------------------------------------- | ------------------------------------------ |
| Job      | Pre-trade **veto**                    | Sizing + **exit** management               |
| Has      | Shariah policy, ledger, kill-switch   | Real equity/DD state, 60m price window, ATR SL/TP, sizer |
| Missing  | **Any real data** — fed by null stubs | Ledger, Shariah, VaR, admin halt, API      |

The fix for the inert guardrails is therefore *not* more thresholds — it is
wiring the `PriceFeed`/`RiskData` interfaces to state that `RiskManager` and
`Portfolio` already maintain.

```
order intent (strategy signal OR POST /orders)
        |
        v
  +---------------------------------+
  | TIER 1 - Guardrails.gate_trade  |  veto + ledger, one chokepoint
  |  Shariah whitelist              |
  |  spot-only / long-only          |  <- absorbed from app/compliance.py
  |  admin halt                     |
  |  30d drawdown (warn/halt)       |  <- fed by real equity history
  |  flash-crash                    |  <- fed by real price window
  |  VaR step-down                  |  <- fed by real daily returns
  |  kill-switch (N breaches/24h)   |
  +---------------------------------+
        | allow / stepdown
        v
  +---------------------------------+
  | TIER 2 - RiskManager            |  sizing + exits, per position
  |  sizer(equity, price)           |
  |  SL/TP levels (pct or ATR)      |
  |  max_concurrent_pos             |
  +---------------------------------+
        |
        v
  ExecutionManager.submit()  ->  PaperBroker | CCXTBroker (hard-off)
```

**The load-bearing structural change:** every order — strategy-generated *and*
API-submitted — passes through Tier 1. Today the API path gates but never
executes, and the engine path executes but never gates.

---

## Canonical choices

For each duplicated concern, one survives — chosen on "which one actually
works", not "which one is newer".

| Concern         | Canonical                              | Deleted                                              | Why |
| --------------- | -------------------------------------- | ---------------------------------------------------- | --- |
| Package root    | `src/intradyne/`                       | top-level `intradyne/`, `src/{risk,core,api,sor,adapters}` | Collapse 3 shim layers to 1 real tree |
| FastAPI app     | `src/intradyne/api/app.py`             | `intradyne/api/app.py`, `app/server.py`              | It's what ships; the others are stubs |
| Settings        | `app/config.py` shape, moved in        | `src/core/config.py`                                 | Nested `RiskConfig`/`FeesConfig` is the better model |
| Ledger          | `app/ledger.py` `ExplainabilityLedger` | `src/core/ledger.py` `Ledger`                        | Caches `_last_hash`; the other re-reads the file per append (O(n^2)) |
| Risk veto       | `Guardrails`                           | —                                                    | Keep, but feed it real data |
| Risk sizing     | `RiskManager`                          | —                                                    | Keep as Tier 2 |
| Compliance      | fold into `ShariahPolicy`              | `app/compliance.py`                                  | The gate must include spot-only/long-only |
| Strategies      | `app/strategies/`                      | `src/strategies/`                                    | The latter are all `pass` — non-functional |
| Engine entry    | `app/main.py` -> lifespan task         | `src/engine.py`                                      | The latter is a synthetic random-walk stub |
| Whitelist       | `app/whitelist.json`                   | `ALLOWED_SYMBOLS` default                            | One source; env becomes an override |

> **Ledger format conflict.** `Ledger` writes `hash_prev`; `ExplainabilityLedger`
> writes `prev_hash`, and they hash different byte ranges. Pick one schema and
> write a one-shot converter for the existing chains. Do not silently append
> records the old verifier cannot follow.

---

## Phases

Each phase leaves the repo in a more honest state than it found it.

### Phase 0 — Stop the bleeding (size S)

Independent of the merge. All of it is either a live vulnerability or an active
misrepresentation.

1. [x] **Fix `require_api_key`** (`src/intradyne/api/deps.py`). Bind the header via
   `Header(alias="X-API-Key")` — with no marker FastAPI binds it as a *query
   parameter*. Delete the internal `API_AUTH_REQUIRED` re-check so `app.py`'s
   decision is authoritative; **fail closed** when auth is required but no key
   is configured. *Currently prod runs unauthenticated.*
2. [x] **Validate `symbol`/`tf`** in `routes/data.py` against the whitelist and a
   timeframe enum. Closes read *and* write path traversal.
3. [x] **Authenticate the WebSockets.** `/ws/ledger` streams the full audit trail
   unauthenticated. Keep the rate-limit exclusion.
4. [x] **CORS** — refuse to start if `FRONTEND_ORIGINS` is `*` while
   `allow_credentials=True`.
5. [x] **`EXPLAIN_LEDGER_PATH`** in prod compose. The ledger
   cannot currently write under `read_only: true`.
6. [x] **Delete `src/engine.py`**, `trading_summary.csv`, `portfolio_snapshot.json`,
   and the test that exists only to assert the stub wrote its files. Fix the
   root `Dockerfile` `CMD` and its unsatisfiable `pgrep` healthcheck.
7. [x] **`git rm -r --cached artifacts/`** plus the tracked ledgers and `optuna.db`.
   Untracked 233 files (488 -> 255 tracked); all remain on disk.
   `artifacts/production_params.json` stays tracked -- it is a runtime
   *input* loaded by `app/main.py`, not generated output.

**Exit:** no unauthenticated write path; no fabricated performance data in the
tree; ledger writes survive production.

### Phase 1 — One package, one config, one ledger (size M)

8. Move `app/*` -> `src/intradyne/engine/*`. Delete the top-level `intradyne/`
   shims and the `src/{risk,core,api,sor,adapters}` duplicates.
9. Merge the two `Settings` classes into `src/intradyne/config.py`. **One
   `.env.example`**, documenting the currently-undocumented `API_AUTH_REQUIRED`,
   `X_API_KEY`, `ADMIN_SECRET`, `FRONTEND_ORIGINS`, `OPENAI_API_KEY`.
10. Merge the two ledgers; add `verify_chain()` — the chain is written today but
    nothing ever checks it. Add `fsync` on append and a file lock.
11. Delete `_prev/`, the `@`-prefixed parallel tree, and `constraints.txt`
    (stale, unused, contradicts `requirements.txt`).
12. **Cache `load_settings()`** (`functools.lru_cache`). It is currently called
    *per request* by `general_rate_limit` and `ai_rate_limit`, re-parsing `.env`
    and re-running validation every time. In production that validation raises
    when broker credentials are absent, so a credential-less deployment returns
    500 on **every** request — including `/healthz` — even though the API places
    no orders. Found while testing the Phase 0 auth fix.
13. **Move Prometheus metric registration into a factory.** `routes/research.py`,
    `routes/data.py` and `risk/guardrails.py` register collectors at module
    scope, so importing the app twice in one process raises
    `DuplicateTimeseries`. This blocks per-test app construction in Phase 4, and
    the obvious workaround (clearing the default `REGISTRY`) is global state
    that breaks `tests/test_metrics_endpoint.py`.

**Exit:** `grep -rn "from src\." src/` returns nothing; one `Settings`; one
ledger with a verifier.

### Phase 2 — Merge the engine into the service (size L)

14. Replace the deprecated `@app.on_event("startup")` with a **lifespan** owning
    the trading loop as a supervised task with restart-on-crash.
15. **Route every order through Tier 1.** `ExecutionManager.submit()` calls
    `gate_trade` before touching a broker. `POST /orders` stops fabricating
    UUIDs and submits to the same `ExecutionManager`.
16. Fold `assert_whitelisted` / `enforce_spot_only` / `forbid_shorting` into
    `ShariahPolicy.check()`.
17. Unify the halt: one `is_halted()`, checked in `gate_trade`, reachable from
    `POST /admin/halt`. Delete the shadow `_halt_enabled` global.
18. Single `Dockerfile`. Keep `read_only: true` — it is a good control.
19. **Startup assertion refusing to boot with live trading enabled** (see
    Phase 5). Lands here, not in Phase 5.

**Exit:** one container trades in paper mode and serves the API; an order
rejected by guardrails never reaches a broker.

### Phase 3 — Activate the risk engine (size M)

The phase that makes the product claim true.

20. Implement `PriceFeed` against `RiskManager.state.symbol_windows` — the 60m
    price window already exists and is already maintained.
21. Implement `RiskData` against a **persistent equity history** in SQLite
    (`DB_URL` already points at `trades.sqlite`). **Must survive restarts:**
    with empty in-memory history `dd_30d([]) == 0.0` and the halt silently
    disarms — today's bug in a new costume.
22. Fix `/risk/status` breach counting — filter on `event == "guardrail_breach"`
    to match `_recent_breach_count`; it currently counts every ledger record.
23. Reconcile the two drawdown definitions: `RiskManager` uses
    `1 - current/start` (session-relative), `dd_30d` uses peak-to-trough over
    30d. Both are defensible; they must not be reported as the same number.

**Exit:** a synthetic 25% drawdown halts trading; a 35% 1h drop pauses it; each
writes a verifiable ledger entry. None of these assertions exist today.

### Phase 4 — Test and CI credibility (size M)

24. Point tests at the shipped app. `test_readyz_sqlite_ok` asserts against a
    hardcoded `return {"ready": True}`; `test_admin_halt_toggle_sequence`
    exercises a global that order gating never reads. Both pass and prove
    nothing. **Until Phase 0 added `intradyne/api/models.py`, the shipped
    `src/intradyne/api/app.py` could not be imported under `pytest.ini`'s path
    order at all** — the deployed app had literally zero coverage.
25. Add the tests that matter: guardrail activation, gate-before-broker
    ordering, auth fails closed, traversal rejected, ledger chain verification,
    live-mode gating.
26. Pin the toolchain. Local Python 3.14 cannot collect the suite (`starlette`
    needs `httpx2`); CI runs 3.11.
27. Widen mypy past the four modules it checks today. `app/router.py` (615
    lines, the actual trading logic) is entirely unchecked.

### Phase 5 — Live readiness (deferred; defined now)

Not this phase, but built toward, so enabling it is a config change plus a
checklist rather than a refactor.

28. **Triple gate:** `MODE=live` AND `LIVE_TRADING_ENABLED=true` AND
    `not is_halted()`.
29. Idempotency keys on submission; reconciliation against exchange state on
    restart; testnet soak; per-symbol and daily notional caps; alerting on
    halt/kill-switch through the existing Grafana contact points.

---

## Documentation

Folded into each phase rather than treated as its own. `README.md` describes
`app/`; `ARCHITECTURE.md` describes `src/`; `RUNBOOK.md` documents five
endpoints that do not exist (`/ops/ping`, `/ops/test_connectors`,
`/profiles/apply`, `/watcher/stop`, `/analytics/latency`) plus IBKR options and
Alpaca accounts belonging to the archived predecessor. Also fix the venue
mismatch: docs say Bitget, `routes/data.py` and `routes/ws.py` hardcode
`api.binance.com`.

---

## Risks

- **Phase 2 is the risky one** — it touches the order path. Do it behind
  `ENGINE_ENABLED=false` so the API keeps serving while the loop stabilizes.
- **Ledger migration is one-way.** Convert in a scripted, reviewable step; keep
  the originals until `verify_chain()` passes on the converted file.
- **Equity history is load-bearing for Phase 3.** If it is not durable, the
  guardrails re-break on every restart in a way that looks fine in tests.
- **Scope drift toward live.** Phase 5 stays closed; item 17 is what enforces
  that.
