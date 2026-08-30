# IntraDyne — Consolidation Plan

**Target:** one service, one image, one config, one ledger. Live-capable by
construction, paper-only and hard-gated in this phase.

**Status:** Phases 0-5 complete. The live-trading gate is deliberately still
shut -- the controls exist, but opening it needs a testnet soak and verified
alert delivery, neither of which can be done from a development machine.

---

## Why

*(Historical, as of phase 0. Phase 1 has since collapsed this.)* The repo
contained two unrelated systems:

- `app/` — the real trading engine: momentum/mean-reversion strategies, CCXT
  Bitget adapter, paper broker, stateful risk manager. **Never deployed.**
- `src/` — the FastAPI service: guardrails, explainability ledger, admin
  endpoints. **This is what ships**, and its `POST /orders` returns a fabricated
  UUID without touching a broker.

Every document in the repo describes one or the other, never both, and
`RUNBOOK.md` documents a third system that no longer exists (the archived
`_prev/legacy/intradyne_lite/`, removed in phase 1; the RUNBOOK itself is
still stale). The plan below collapses this to a single service.

---

## The two-tier risk model

`Guardrails` (`intradyne/risk/guardrails.py`) and `RiskManager`
(`intradyne/engine/risk.py`)
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
works", not "which one is newer". Paths below are pre-phase-1.

| Concern         | Canonical                              | Deleted                                              | Why |
| --------------- | -------------------------------------- | ---------------------------------------------------- | --- |
| Package root    | `src/intradyne/`                       | top-level `intradyne/`, `src/{risk,core,api,sor,adapters}` | Collapse 3 shim layers to 1 real tree |
| FastAPI app     | `src/intradyne/api/app.py`             | `intradyne/api/app.py`, `app/server.py`              | It's what ships; the others are stubs |
| Settings        | `app/config.py` shape, moved in        | `src/core/config.py`                                 | Nested `RiskConfig`/`FeesConfig` is the better model |
| Ledger          | `app/ledger.py` `ExplainabilityLedger` | `src/core/ledger.py` `Ledger`                        | Caches `_last_hash`; the other re-reads the file per append (O(n^2)) |
| Risk veto       | `Guardrails`                           | —                                                    | Keep, but feed it real data |
| Risk sizing     | `RiskManager`                          | —                                                    | Keep as Tier 2 |
| Compliance      | fold into `ShariahPolicy`              | `app/compliance.py`                                  | The gate must include spot-only/long-only |
| Strategies      | `app/strategies/`                      | *(kept — see phase 1 note)*                          | Different concerns, not duplicates |
| Engine entry    | `app/main.py` -> lifespan task         | `src/engine.py`                                      | The latter is a synthetic random-walk stub |
| Whitelist       | `app/whitelist.json`                   | `ALLOWED_SYMBOLS` default                            | One source; env becomes an override |

> **Ledger format conflict — resolved in phase 1.** `Ledger` wrote `hash_prev`,
> `ExplainabilityLedger` wrote `prev_hash`, and they hashed different byte
> ranges. Unified on the `hash_prev` schema because that is what the existing
> on-disk ledgers contain; `verify_chain()` confirms they validate unchanged,
> so no converter was needed.

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

8. [x] Move `app/*` -> `src/intradyne/engine/*`. Delete the top-level `intradyne/`
   shims and the `src/{risk,core,api,sor,adapters}` duplicates.
9. [x] Merge the two `Settings` classes into `src/intradyne/config.py`. **One
   `.env.example`**, documenting the currently-undocumented `API_AUTH_REQUIRED`,
   `X_API_KEY`, `ADMIN_SECRET`, `FRONTEND_ORIGINS`, `OPENAI_API_KEY`.
10. [x] Merge the two ledgers; add `verify_chain()` — the chain is written today but
    nothing ever checks it. Add `fsync` on append and a file lock.
11. [x] Delete `_prev/`, the `@`-prefixed parallel tree, and `constraints.txt`
    (stale, unused, contradicts `requirements.txt`).
12. [x] **Cache `load_settings()`** (`functools.lru_cache`). It is currently called
    *per request* by `general_rate_limit` and `ai_rate_limit`, re-parsing `.env`
    and re-running validation every time. In production that validation raises
    when broker credentials are absent, so a credential-less deployment returns
    500 on **every** request — including `/healthz` — even though the API places
    no orders. Found while testing the Phase 0 auth fix.
13. [deferred to phase 4] **Prometheus metric registration in a factory.**
    Deferred after measuring: repeated `create_app()` already works, because
    the collectors register at module *import*, which happens once.
    `DuplicateTimeseries` only fires on module re-import, which neither
    production nor the test suite does. There is no production impact and the
    only consumer is test infrastructure, so it belongs with the Phase 4 test
    work rather than being churn now.

**Exit (met):** `grep -rn "from src\." src/` returns nothing; one `Settings`;
one ledger with a verifier. Tracked files 488 -> 161; suite 34 -> 78 passing.

Two findings corrected the plan as written. `src/strategies` was slated for
deletion as "all `pass`" -- in fact only the four profile subclasses are empty
and the subsystem is portfolio-weight allocation, a different concern from the
tick-level signal generators, so it was preserved (see ARCHITECTURE.md). And
the two drawdown settings turned out to measure different things, so they were
deliberately kept separate rather than merged.

### Phase 2 — Merge the engine into the service (size L)

14. [x] Replace the deprecated `@app.on_event("startup")` with a **lifespan** owning
    the trading loop as a supervised task with restart-on-crash.
15. [x] **Route every order through Tier 1.** `ExecutionManager.submit()` calls
    `gate_trade` before touching a broker. `POST /orders` stops fabricating
    UUIDs and submits to the same `ExecutionManager`.
16. [x] Fold `assert_whitelisted` / `enforce_spot_only` / `forbid_shorting` into
    `ShariahPolicy.check()`.
17. [x] Unify the halt: one `is_halted()`, checked in `gate_trade`, reachable from
    `POST /admin/halt`. Delete the shadow `_halt_enabled` global.
18. [x] Single `Dockerfile`. Keep `read_only: true` — it is a good control.
19. [x] **Startup assertion refusing to boot with live trading enabled** (see
    Phase 5). Lands here, not in Phase 5.

**Exit (met):** one image serves the API and hosts the trading loop; an order
rejected by the gate provably never reaches a broker, and both the refusal and
the fill are recorded in one hash-chained ledger. The loop ships behind
`ENGINE_ENABLED=false` -- the machinery is in place, switching it on is a
separate step. Suite 78 -> 112 passing.

Three defects surfaced while wiring this up and were fixed here: the long-only
rule permitted selling more than was held (only the paper broker's silent
clamp hid it, and the live path would have shorted the difference); the ledger
recorded a hardcoded `{"whitelist": True, ...}` as though it were the outcome
of the compliance checks; and loguru's default `diagnose=True` renders local
variable *values* in tracebacks, so one engine crash would have written the
broker credentials into `app.log`.

### Phase 3 — Activate the risk engine (size M)

The phase that makes the product claim true.

20. [x] Implement `PriceFeed` against `RiskManager.state.symbol_windows` — the 60m
    price window already exists and is already maintained.
21. [x] Implement `RiskData` against a **persistent equity history** in SQLite
    (`DB_URL` already points at `trades.sqlite`). **Must survive restarts:**
    with empty in-memory history `dd_30d([]) == 0.0` and the halt silently
    disarms — today's bug in a new costume.
22. [x] Fix `/risk/status` breach counting — filter on `event == "guardrail_breach"`
    to match `_recent_breach_count`; it currently counts every ledger record.
23. [x] Reconcile the two drawdown definitions: `RiskManager` uses
    `1 - current/start` (session-relative), `dd_30d` uses peak-to-trough over
    30d. Both are defensible; they must not be reported as the same number.

**Exit (met):** a synthetic 25% drawdown halts trading; a 35% 1h drop pauses
it; each writes a ledger entry and the chain verifies. Proven both at the gate
and end-to-end through the HTTP order path. Suite 112 -> 127 passing.

Two further defects were found and fixed here. The kill-switch was unreachable
in its most common case: the flash-crash branch returns "pause" as soon as it
trips, and the kill-switch check sat after it, so repeated breaches could never
escalate to a halt -- the entire purpose of a kill switch. It is now checked
before the metric guardrails. And the test suite was writing equity rows and
ledger entries into the repository's own tracked `data/trades.sqlite`; the
database is now untracked and every test runs against a temporary one.

### Phase 4 — Test and CI credibility (size M)

24. [x] Point tests at the shipped app. `test_readyz_sqlite_ok` asserts against a
    hardcoded `return {"ready": True}`; `test_admin_halt_toggle_sequence`
    exercises a global that order gating never reads. Both pass and prove
    nothing. **Until Phase 0 added `intradyne/api/models.py`, the shipped
    `src/intradyne/api/app.py` could not be imported under `pytest.ini`'s path
    order at all** — the deployed app had literally zero coverage.
25. [x] Add the tests that matter: guardrail activation, gate-before-broker
    ordering, auth fails closed, traversal rejected, ledger chain verification,
    live-mode gating.
26. [x] Pin the toolchain. Local Python 3.14 cannot collect the suite (`starlette`
    needs `httpx2`); CI runs 3.11.
27. [x] Widen mypy past the four modules it checks today. `app/router.py` (615
    lines, the actual trading logic) is entirely unchecked.
    Now checks 45 source files and passes. `intradyne/engine/` stays excluded
    with an explicit TODO: 75 of its errors are in `router.py` alone and need
    an annotation pass, which is its own task rather than a rushed one here.

**Exit (met):** every CI gate passes -- ruff check, ruff format, mypy, pytest
(133) -- and the suite is green for the first time. CI itself was previously
**red on every run**: `test_config.py` asserted that `BITGET_API_*` were set
in the environment, which CI never provides.

Found and fixed here, all of it invisible while the gates were unreliable:

- `/readyz` called `os.makedirs()` and opened the database in create mode, so
  the readiness probe *provisioned the dependency it was reporting on* and
  could never fail. It also wrote to the filesystem on an unauthenticated GET,
  which would break under the `read_only` container root.
- `test_config.py` mutated `os.environ` globally with no cleanup, leaking
  values out of `.env` files into every later test.
- `intradyne/data/api_feed.py` and `intradyne/backtester/__main__.py` could not
  be imported at all -- they referenced six functions that do not exist in this
  tree. Nothing imported them, so nothing noticed. Both deleted.
- A test asserting `status_code in (200, 404)` could not fail.
- The supervisor restart test was timing-based and intermittently failed; it
  now waits on an event.
- `StrategyRouter`'s sentiment knobs were attached from outside and read back
  through `hasattr`/`getattr`, hiding typos. Declared properly.

13. [closed, not done] **Prometheus registration in a factory.** Re-measured:
    repeated `create_app()` works, nothing re-imports modules, so there is
    still no consumer. Closing rather than deferring again -- doing the churn
    would not make any gate more trustworthy.

### Phase 2.5 — defects found reviewing my own work

Four problems the phase exits did not catch, because the tests exercised paths
that avoided them.

28. [x] **The hosted loop ignored tuned parameters.** `build_router` was called
    with no `params`, so `STRATEGY_PARAMS_FILE` / `production_params.json` and
    their risk overrides were silently dropped and the engine always ran
    strategy defaults -- while the README documented the opposite.
29. [x] **Equity was recorded only on a paper fill.** With no orders the series
    never grew, unrealised losses were invisible, and the live path returned
    before recording at all. A book down 30% without trading showed zero
    drawdown -- exactly when the halt is needed. Now sampled on a timer on the
    tick path, seeded at loop start, and recorded on live fills.
30. [x] **`engine/main.py` still built a parallel stack** -- its own portfolio,
    paper broker, ledger, execution manager and FastAPI app -- so
    `python -m intradyne.engine.main` ran a second system with separate state.
    It is now a thin entrypoint serving the one canonical app with the loop
    enabled. `engine/server.py` is deleted and its `/state` and `/profile/*`
    capability moved to `/engine/*` on the API, acting on the *running* router.
31. [x] **The real tick path had never executed.** Every engine test
    monkeypatched `run_once`. It is now driven by an injectable scripted feed,
    which is what would have caught 28 and 29.

**Verified in the image:** `/engine/status` reports `running: true`, and
`/risk/status` shows equity recorded with no order placed. The venue was
unreachable from the container, so symbol resolution fell back to the
unfiltered whitelist as designed.

### Phase 5 — Live readiness (controls built; gate still shut)

28. [x] **Triple gate:** `MODE=live` AND `LIVE_TRADING_ENABLED=true` AND
    `not is_halted()`. The halt is now enforced at the live broker boundary as
    well as in the gate, so any caller reaching the broker directly is covered.
29. [x] **Idempotency keys.** Deterministic client order ids, claimed locally
    *before* the venue is contacted and sent as `clientOrderId`. A crash
    mid-submit cannot become a second real order, and a failed submission
    keeps its claim rather than freeing the key -- the venue may have received
    it.
30. [x] **Restart reconciliation.** Unresolved claims halt trading. It
    deliberately does not guess: re-sending risks doubling a position,
    discarding risks trading against an unknown one. A human checks the venue.
31. [x] **Exposure caps.** Per-order, per-symbol 24h, and total 24h notional,
    durable across restarts. The risk guardrails bound volatility but nothing
    bounded transacted volume: a strategy looping on a bad signal could place
    unlimited orders each small enough to pass every threshold. An order whose
    notional cannot be evaluated is refused.
32. [x] **Safety alerting.** Seven Prometheus gauges refreshed at scrape time,
    and a new `intradyne-safety` alert group. The existing rules watched
    infrastructure only, so nothing would have paged when the system halted
    itself or left live orders unreconciled.
33. [ ] **Testnet soak.** Cannot be done from a development machine. See
    RUNBOOK section 8.

**Exit:** the controls are built and tested (163 tests). The gate stays shut.
`LIVE_TRADING_GATE_OPEN` is a code constant, not an environment variable, so
opening it leaves a reviewable commit -- and a test asserts it is still
`False`.

**Before opening it** (RUNBOOK section 8): testnet soak; confirm an alert
actually reaches a human; set the caps, which default to disabled; rehearse
the halt; establish from honest backtests that the strategy has an edge after
fees; and obtain a scholarly ruling on whether high-frequency scalping is
itself acceptable, which no code can decide.

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

---

## Open: PnL does not reconcile with the stated geometry

**Do not treat any backtest result as evidence of an edge until this is
resolved.**

After capping cumulative position size, a run on real ETH 1m data reports:

| quantity          | value                                   |
| ----------------- | --------------------------------------- |
| peak position     | $150 (correct: 1.5% of a 10k book)      |
| take-profit       | 80 bps                                  |
| max win per trade | ~$1.20 (0.8% of $150)                   |
| **observed average win** | **$15.07**                       |
| net pnl           | +$630 over 110 round trips              |

A winner cannot be worth $15 when the target caps it near $1.20 on a $150
position. The arithmetic is out by roughly 12x, so at least one of these is
wrong: the realised-PnL attribution, the exit price actually taken, or the
position size at the moment of exit.

Two candidate explanations, neither verified:

1. **Slice aggregation.** Exits are micro-sliced and partial take-profits
   close half a position at a time. Round trips are counted from per-tick
   changes in `realized_pnl`, so several slices closing on one tick collapse
   into a single "win". That would inflate the average win and depress the
   trade count, but should not change total PnL.
2. **Exits above the take-profit.** If positions exit well beyond the target
   the realised gain per trade exceeds what the geometry allows, which would
   mean the take-profit is not binding even after the time-stop fix.

Until it reconciles, `expectancy_pct` cannot be trusted, and a
`clears_with_margin` verdict means nothing. The next step is to run with
`fast_mode=False` and read `trades.jsonl` directly: entry price, exit price,
quantity and exit reason per round trip, checked by hand against the
configured levels.
