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

## Resolved: PnL now reconciles

The ~12x gap had two causes, and my framing of it was itself wrong.

**My framing was wrong.** I divided total profit by an aggregated win count
and compared it against a single-position take-profit. But the position turns
over constantly -- 1,206 buys and 328 sells in the window -- so "average win
per round trip" was never comparable to "take-profit on one position".
Reconstructing cash flow independently from the fills matched
`Portfolio.realized_pnl` **to the cent**: the accounting was never broken.

**Bug 1 -- the closing liquidation used equity as a price.** The end-of-window
liquidation priced the position at `eq_curve[-1]`, which is portfolio
*equity*, not a price. One ETH fill sold at `$9,980` while the market was
`$1,875`, realising `$647.76` -- essentially the entire `+$646` reported
profit of that run, from a single fabricated fill. On a higher-priced
instrument it fabricates an equally large loss. Now priced at the last traded
price, with a regression test asserting no fill lands outside the
instrument's price band.

That one fix moved the run from **+$629.75 to -$17.76**.

**Bug 2 -- expectancy was theory, not measurement.** `assess()` answers "what
win rate would this geometry need *if* every win were exactly `tp` and every
loss exactly `sl`". That is a target-setting question, not a measurement.
Exits do not respect those levels: only 4% of them reached the +80bps target
while 46% breached the -20bps stop, down to -85bps, because a stop gaps
through between bars and a target may never be touched. So a run could report
`clears_with_margin` while losing money.

Summaries now carry `realized_return_bps`, measured from actual PnL over
capital deployed, and a verdict of `clears_with_margin` or `marginal` is
overridden to **`contradicted_by_realized`** when the realised return is not
positive. The two disagreeing is now the signal that exits are not honouring
the configured levels.

The run that prompted all of this now reports honestly:

| quantity              | value                                   |
| --------------------- | --------------------------------------- |
| net pnl               | **-$17.76** (was reported +$3,503)      |
| realised return       | **-10.76 bps** per unit of capital      |
| theoretical expectancy| +6.00 bps (assumed geometry)            |
| verdict               | **`contradicted_by_realized`**          |

**Still true: no edge is established.** The measurement is now trustworthy,
and what it says is that this configuration loses money.

---

## Why it loses: holding period vs cost, not signal quality

With the stop anchored to average cost, the liquidation priced correctly and
expectancy measured from realised outcomes, the measurement is trustworthy.
What it shows is not a weak entry signal but an incompatible cost structure.

ETH/USDT realised volatility is **0.32 bps per second**. Over a holding period
the expected absolute move scales with the square root of time:

| hold                       | expected move |
| -------------------------- | ------------- |
| 1 min                      | 2.5 bps       |
| **2 min** (shipped time stop) | **3.5 bps** |
| 10 min                     | 7.9 bps       |
| 30 min                     | 13.7 bps      |
| 1 hour                     | 19.4 bps      |

Round-trip cost is **14 bps** taker. The instrument moves 3.5 bps over the
intended holding period, so the fee is **four times the entire expected move**.
No entry signal overcomes that: you are paying 14 bps to capture something
whose expected magnitude is 3.5.

Breakeven holding period, purely from volatility against cost:

- **taker both legs (14 bps): ~31 minutes** — not scalping
- **maker both legs (4 bps): ~2.6 minutes** — the intended horizon

Confirming this, at the designed 1s timescale the take-profit is unreachable:
20 bps is a 62-sigma one-bar move, and `tp=20/sl=30` and `tp=80/sl=20` produce
near-identical results (-14.96 vs -14.99 bps realised, 422 vs 420 round trips)
because neither level is ever touched and everything exits on the time stop.

**So the binding constraint is fee structure, not strategy.** The single
highest-leverage change is earning maker fees rather than paying taker: it
moves the viable holding period from ~31 minutes to ~2.6, which is the horizon
the strategy was built for. That requires posting resting limit orders and
accepting non-fills, which the execution path does not currently do — it
submits market orders throughout.

### Stop overshoot: verified as discretization, not a defect

After anchoring the stop to average cost, exits still land past it. Measured
over 94 stop-triggered exits on real ETH 1m data:

| measure                              | value                    |
| ------------------------------------ | ------------------------ |
| bars between stop breach and fill    | **0** (100% of exits)    |
| median overshoot past the stop       | +5.9 bps (**0.9 sigma**) |
| p90 / max overshoot                  | +15.6 / +28.4 bps        |

Every exit fills on the bar the stop is breached -- there is no queuing or
lag defect. The overshoot is the unavoidable consequence of evaluating stops
on bar closes: a mid-bar crossing fills at the close, roughly one standard
deviation past on average. The worst case reconciles exactly: -20 bps stop
plus -28.4 bps overshoot = the -48.4 bps observed.

**Implication for the cost model, and it is not favourable.** The *effective*
stop is the configured distance plus ~6 bps, so a "20 bps" stop realises
about -26 bps. Breakeven should be computed from realised win/loss rather
than configured `tp`/`sl`; `realized_return_bps` already measures the outcome
directly, which is why it is the field to trust.

Live this will be less severe than in a 1m backtest -- a real venue fills on
tick data, not bar closes -- but it will not be zero, and it is one more
reason the taker cost structure does not survive a 2-minute holding period.

### Maker execution: the bar backtest said no, tick data says yes

I recommended maker fills as "the single highest-leverage change". Measured,
that was wrong for this strategy on this data.

Two modelling flaws had to be fixed first, or the feature would have
fabricated its own success:

- **A marketable limit was booked as a maker fill.** A buy limit at or above
  the ask crosses the spread -- that is a taker fill -- but it was credited
  the maker rebate at the limit price. Posting limits would have looked free.
- **Resting orders were never re-checked.** `_try_fill` ran once at
  submission, so a passive order that did not fill immediately stayed open
  forever. `PaperBroker.on_tick` now sweeps the book each quote, with a TTL.

A third error was mine, in the first cut: posting *exits* passively too. A
passive stop is not a stop -- it rests unfilled exactly when the market is
running away, leaving the position open and, because it still counts against
`max_concurrent_pos`, blocking all further trading. That took a 422-trade run
down to one. Long-only makes the fix clean: entries post, exits cross.

Results on real ETH data:

| data        | mode  | round trips | win rate | realised |
| ----------- | ----- | ----------- | -------- | -------- |
| 1m, 7 days  | taker | 149         | 32.9%    | -12.10 bps |
| 1m, 7 days  | maker | 144         | **27.8%** | **-14.52 bps** |
| 1s, 24h     | taker | 422         | 11.8%    | -14.96 bps |
| 1s, 24h     | maker | 12          | 16.7%    | +11.89 bps *(insufficient data)* |

**Maker execution made it worse on the sample that has enough trades to
judge.** The fee saving is real but smaller than the adverse selection it
buys: you fill only when price ticks down to your bid, which is precisely the
signals where the breakout immediately failed. The win rate drop from 32.9%
to 27.8% is that effect, and it outweighs the 5bps saved on the entry leg.

**Caveat on the model.** The CSV bars carry no bid/ask, so `bid = ask = last`
and "posting at the bid" is really "posting at the last price", filled by any
downtick. That is a reasonable proxy but it likely *overstates* adverse
selection, since a real resting order sits inside a spread and is filled by
someone crossing it. Settling whether maker execution helps needs order-book
or tick data, not OHLCV bars.

### Corrected on tick data: maker execution does help

The conclusion above was drawn from bar data and is wrong. Repeating it
against real trade ticks reverses it.

OHLCV carries no bid/ask, so the bar backtest had `bid = ask = last` -- a
zero spread -- and a resting order was filled by any downtick whatsoever.
That is the worst possible case for a passive order and it overstated adverse
selection badly. Binance aggTrades carries `was_buyer_maker`, giving the
aggressor side of every trade, from which a genuine L1 quote can be
reconstructed. On 2026-08-28 ETHUSDT that yields **985,369 trades and a median
spread of 0.52 bps**.

Same strategy, same parameters, 2 hours of ETH ticks:

| mode  | trips | win rate | realised | maker fills | taker fills |
| ----- | ----- | -------- | -------- | ----------- | ----------- |
| taker | 55    | 21.8%    | -15.41 bps | 0         | 6,777       |
| maker | 55    | **32.7%** | **-10.56 bps** | 313  | 303         |

**Maker execution improves realised return by 4.85 bps**, and the theoretical
saving on the entry leg is 5 bps (taker 5 + slippage 2, against maker 2). The
agreement is close enough to be convincing. The fill split is exactly the
intended design: 313 maker fills are the entries, 303 taker fills the exits.

**Which measurement to trust.** The tick run, without hesitation. The question
is entirely about what happens between the bid and the ask, and the bar data
has no bid or ask to reason with. A model that cannot represent the spread
cannot answer a question about the spread.

Confirmed on a larger sample. Four hours, 140,533 quotes, median spread 0.48
bps, both runs past the 100-round-trip threshold:

| window | mode  | trips | win rate | realised   |
| ------ | ----- | ----- | -------- | ---------- |
| 2h     | taker | 55    | 21.8%    | -15.41 bps |
| 2h     | maker | 55    | 32.7%    | -10.56 bps |
| 4h     | taker | 105   | 19.0%    | -16.52 bps |
| 4h     | maker | 106   | 29.2%    | -11.47 bps |

The maker gain is 4.85 bps at two hours and 5.05 at four -- stable, and
matching the 5 bps the entry leg saves. The effect is real and reproducible.

**It is still nowhere near enough.** -11.47 bps is a loss, and breakeven at
tp=20/sl=30 needs an 88% win rate against 29.2% achieved. Maker execution
closes about a third of the gap to zero and none of the gap to profit. Every
configuration tested -- across bars and ticks, both execution modes, five
payoff geometries, two timescales -- loses money, and the losses cluster
tightly between -10 and -17 bps per unit of capital.

That consistency is itself informative. The strategy is not marginally
unprofitable in a way that parameter tuning might rescue; it is losing
roughly the round-trip cost on every trade, which is what a signal with no
predictive power does.

## Fifty signals, and why none of them is the answer

Asked for fifty strategies ranked into a top five. Built the fifty
(`scripts/strategy_search.py`), and the ranking is real, but the top five is
not a shortlist — it is a demonstration of selection bias, so the screen was
built to say so rather than to hand over five numbers.

Ranking N strategies and reporting the best is a biased estimator: the maximum
of N noisy measurements sits above the truth even when every one of them is
worthless. With fifty candidates it is almost impossible *not* to produce five
profitable-looking strategies. Three guards were built in.

- **Shared exit mechanics.** `forward_outcomes` computes the net result of
  entering at every bar once; a strategy is only a boolean mask over that. No
  strategy can win by accidentally getting a different exit rule, and when a
  bar spans both target and stop it is scored as the stop, since bar data
  cannot say which came first.
- **Held-out days.** Ranked on 26–27 Aug, re-measured on 28–29 Aug.
- **A null threshold.** Random entry rules with matched trade counts are drawn
  to build the distribution of *best-of-fifty under no edge*. A strategy has
  to clear that, not merely clear zero.

Fifty-four signals cleared the 100-trade minimum, spanning momentum, mean
reversion, breakout, EMA cross, volatility regime, trade intensity, VWAP
deviation, and order-flow imbalance — the last being the only family tick data
makes available at all, via the aggressor side of each trade.

| # | strategy | trades | win | train | held out |
|---|---|---|---|---|---|
| 1 | revert_300s_k2.5 | 166 | 27.1% | −11.73 | −12.74 |
| 2 | vwap_dev_300s_k2 | 231 | 24.7% | −11.92 | −13.60 |
| 3 | revert_300s_k1.5 | 299 | 20.7% | −12.04 | −14.09 |
| 4 | intensity_30s | 219 | 25.1% | −12.18 | −13.55 |
| 5 | breakout_120s | 313 | 21.7% | −12.42 | −13.52 |

Entering at random returns −13.30 bps. Best-of-54 under no edge is −10.41 bps.
Every one of the fifty-four landed between −11.7 and −12.7: **none reached the
null threshold**, and the spread across fifty-four different ideas is narrower
than what random selection alone produces. Gross of costs these signals earn
+1.3 to +2.3 bps against a 14 bps round trip.

### It is not the fee schedule, and not the geometry

`scripts/strategy_sweep.py` re-runs the whole library across holding periods,
payoff geometries, and fee assumptions down to zero.

| geometry | best gross | vs null (no cost) |
|---|---|---|
| tp20/sl30, 120s | +1.27 | null +1.76 — below |
| tp40/sl20, 300s | +2.27 | null +3.77 — below |
| tp60/sl40, 900s | +4.77 | null +8.57 — below |

The gross edge does grow with holding period, which is the one encouraging
number here. It never grows faster than the null. **Even at zero fees and zero
slippage, no signal beats the best of fifty random entry rules** on this
sample.

(Superseded in part — see "The edge is real, and far too small" below. Four
days is not enough data to detect an effect this size, and with a month of 1s
bars several of these signals do clear the null. The economic conclusion is
unchanged; the claim that they carry *no* information was too strong.)

### The long-horizon mirage

Stretching the horizon until the move can outrun costs looks like it works,
until the sample is counted:

| horizon | best gross | trades |
|---|---|---|
| 900s | +6.55 | 91 |
| 1800s | +6.80 | 65 |
| 3600s | +14.80 | 42 |
| 7200s | +17.68 | 27 |

At 3600s, +14.80 bps clears the 14 bps taker cost. It is also 42 trades with a
per-trade standard deviation of 56 bps — a standard error of ±8.6, so the 95%
interval is [−2.0, +31.6] and straddles zero. Held out it returns −5.51. The
7200s row is the same story with less data. Non-overlapping trades are the
binding constraint: two days of ticks contain at most 37 independent hourly
trades, so the horizons where costs stop dominating are exactly the horizons
where nothing can be measured.

**The honest top five is an empty list.** More signals will not fix this —
fifty across eight families produced a tighter cluster than chance. What would
change the answer is more data (weeks, not days, to make hourly horizons
measurable) or a different instrument, not another entry rule.

## Months of data: the long-horizon door closes too

The fifty-signal screen left one thing genuinely open. Gross edge grew with
holding period, but the long horizons had 27-42 non-overlapping trades, so the
standard error swamped the estimate. That is a sample-size problem, and sample
size is fixable.

`scripts/fetch_klines_archive.py` pulls whole months from the Binance archive
rather than paginating the REST endpoint 1000 bars at a time. 1m bars cost
~2MB a month, so **31 months of ETH and BTC (943 days, 1.36M bars each)** is a
smaller download than one day of aggTrades. Klines carry
`taker_buy_base_volume`, the same aggressor split the tick loader
reconstructs, so the order-flow signals survive the move from ticks to bars.

Two things had to change with a longer sample.

**Drift becomes the thing to beat.** Over months, a long-only rule at an
hour-plus horizon earns whatever the asset did, and that dwarfs costs. Beating
zero proves nothing. Every result below is therefore *excess over random
entry* on the same bars — the unconditional mean, carrying identical drift.
The check that this works: random entry earns +0.11, −0.32, −0.41, +0.35 bps
gross across the four ETH horizons. Essentially zero, on an instrument that
fell 18% over the period. The drift control holds.

**One split becomes walk-forward.** Ranking on a train set and reporting the
winner's test score still flatters, because the winner was chosen by looking.
`scripts/strategy_months.py` instead picks the best strategy on each fold and
trades it on the *next* fold — the number a live deployment would actually
experience.

| instrument | horizon | walk-forward excess over drift | folds positive |
|---|---|---|---|
| ETH | 15 min | −0.09 ± 0.59 | 3/5 |
| ETH | 1 h | −2.70 ± 2.61 | 2/5 |
| ETH | 4 h | −0.17 ± 2.84 | 2/5 |
| ETH | 8 h | **+5.92 ± 2.77** | 4/5 |
| BTC | 1 h | −0.68 ± 1.07 | 2/5 |
| BTC | 4 h | −2.42 ± 1.95 | 2/5 |
| BTC | 8 h | −1.58 ± 1.50 | 2/5 |

The ETH 8-hour row is the only result in this whole effort that did not look
like noise on sight. It does not survive being pushed on.

- **Fold boundaries.** Re-cut into 10 folds it falls to +4.59 ± 4.27, positive
  in 5 of 9 — a coin flip.
- **One fold carries it.** Leave-one-out across those nine folds ranges +1.02
  to +6.21. Dropping the single best fold leaves **+1.02 ± 2.65**.
- **It does not replicate.** BTC is negative at all three long horizons, over
  the same 943 days, having *risen* 49% — so a falling-market excuse is not
  available.
- **It never cleared costs anyway.** Taking the most favourable number in the
  table at face value, +5.92 bps gross against a 14 bps round trip is
  **−8.08 bps net per trade**.

On the full sample, at every horizon on both instruments, every strategy came
in below the best-of-fifty null threshold. Not one "clears null".

### What months of data actually settled

The four-day result could be dismissed as too small a sample. It cannot now.
Across 943 days, two instruments, four holding periods from 15 minutes to 8
hours, and fifty signals in eight families, **nothing beats random entry by
more than its own error bar, and nothing comes within half of round-trip
costs.** The remaining hypotheses worth anything are structural — a different
market, a different instrument class, or a genuine informational input the
price series does not contain — not another entry rule on ETH or BTC.


## The edge is real, and far too small

Re-running the screen on **one month of 1s bars (2.68M bars)** rather than four
days changes one conclusion above and confirms the rest. At a two-minute
horizon, five signals clear the best-of-fifty null threshold, and the
walk-forward excess over drift is **+0.48 bps, positive in 4 of 4 folds**.

That is not a fold-agreement artifact. Per-trade dispersion at this horizon is
only ~9 bps, so the standard errors are genuinely small:

| signal | trades | excess over drift | significance |
|---|---|---|---|
| breakout_300s | 6,391 | +0.49 bps | 4.3 sigma |
| mom_5s_k1 | 18,479 | +0.39 bps | 6.0 sigma |

**So momentum and breakout do carry information about the next two minutes of
ETH.** The earlier "no information" reading was a power problem, not a
finding: detecting a 0.5bps effect against 9bps noise needs on the order of
1,300 trades, and four days of ticks supplied 50-300. A month supplies 6,000
to 18,000, and the effect resolves.

It does not help.

- Round-trip taker cost is **28-35x the excess** (+0.49 vs 14 bps).
- All-maker fills at 4 bps are still **8-10x** the excess.
- Breaking even requires round-trip costs below **~0.5 bps**, about a tenth of
  the best maker-only economics available to a retail account -- and the
  maker-execution work earlier in this file showed maker fills are not free
  in any case, because adverse selection fills you exactly when the move is
  against you.

This is the most precise statement the whole effort supports: **the signal is
real, it is roughly half a basis point, and the cheapest way to trade it costs
several basis points.** The gap is not a tuning problem or a fee-negotiation
problem. It is an order of magnitude.

## A universe that includes the dead

The cross-sectional test needs a universe, and the obvious way to build one is
wrong twice. `scripts/point_in_time_universe.py` recomputes membership at each
rebalance date from data available on that date only.

**583 names have entered the universe since 2018; 180 of them no longer
trade** -- 31% mortality. Those names stay in every snapshot they belonged to,
so a strategy holding one takes the loss it actually took. The dates line up
with real events rather than data artifacts: SRM leaves on 2022-11-28 with
FTX, MATIC on 2024-09-10 at the POL migration, OCEAN and AGIX together on
2024-07-01 when both folded into FET.

Concretely, SRM is a member from 2021-02-27 to 2022-11-19 and absent
thereafter. A survivorship-biased universe contains it on no date at all, and
a momentum strategy run against one never has the chance to lose money on it.

Liquidity is judged the same way -- median quote volume over a window ending
at the rebalance date, never today's volume. Using current liquidity to decide
what was tradeable in 2022 leaks the future as surely as using current
listings.

| date | names in universe |
|---|---|
| 2018-02 | 2 |
| 2020-02 | 36 |
| 2022-01 | 237 |
| 2024-01 | 339 |
| 2026-01 | 327 |

The criteria for the test itself are fixed in
`docs/CROSS_SECTIONAL_PREREGISTRATION.md`, committed before the test was
written. Given that four apparently-profitable results have already dissolved
here and a fifty-signal screen produced a "top 5" that was pure selection
bias, criteria written afterwards would not be worth reading.

## The cross-sectional test: negative

Run against `docs/CROSS_SECTIONAL_PREREGISTRATION.md`, committed before the
script existed. Universe: the 103 unflagged names with data, point-in-time,
survivorship included. 84 monthly periods, top decile equal-weighted, 14bps on
realised turnover.

| signal | excess/period | annualised | Sharpe |
|---|---|---|---|
| low_downside_vol | **+0.455%** | +5.5% | 0.10 |
| volume_trend | -0.045% | -0.6% | -0.01 |
| mom_12m | -0.174% | -2.1% | -0.04 |
| mom_1m | -2.279% | -27.8% | -0.41 |
| mom_6m | -2.666% | -32.5% | -0.62 |
| mom_3m_volscaled | -3.088% | -37.6% | -0.56 |
| mom_3m | -3.352% | -40.8% | -0.58 |
| reversal_1w | -5.454% | -66.4% | -1.32 |

Against the criteria:

- **PASS** excess > 0 — best signal +0.455%/period
- **FAIL** clears the best-of-8 null — null is +4.594%/period
- **FAIL** Sharpe >= 0.8 — measured 0.10
- **FAIL** positive in a majority of folds — 1 of 4

Three of four fail, which the pre-registration defines as a negative result.
No re-run with adjusted parameters.

Notably, seven of eight signals *underperform* equal-weighting the universe,
several by a wide margin. Momentum is not merely absent here; ranking on it
and holding the top decile did materially worse than holding everything.

### The harness was capable of showing a positive

A negative result is worth nothing if the simulation was rigged to produce it,
so that was checked directly: selecting names at random earns **-0.09%** excess
per period against an expected zero, across 300 trials. The machinery is
unbiased, and `test_random_selection_earns_no_excess` pins it.

### A flaw in the pre-registration, stated rather than fixed

The criteria fixed a top-decile portfolio but set no floor on universe size.
The universe starts at 2 names and has a median of 37, so the top decile is
frequently **3 names**. A 3-name portfolio is dominated by idiosyncratic noise:
random selection alone has a standard deviation of 1.74% per period across
trials, which is why the best-of-8 null sits at +4.6%.

That makes this a weak test. Only an enormous edge could have cleared that
bar, so "no edge" and "an edge too small to see through 3-name noise" are not
distinguished by it.

This is a real defect in how the test was specified, and the honest response is
to record it, not to quietly re-run with a universe floor and present that as
the result. A better-powered version -- a size floor, a wider slice, or both --
would be a **new** pre-registered test, with its own criteria fixed in advance.

Whether it is worth running is a separate question. The signals did not merely
fail to clear a high bar; most were solidly negative against a benchmark that
shares their drift and survivorship. That is not the shape of an edge hidden
under noise.

## 100 random intraday strategies: nothing reaches Tier 1

Everything searched before this tested *single* entry signals.
`scripts/random_strategy_search.py` samples whole strategies instead --
entry predicate, confluence requirement, regime filter, exit geometry and
holding period -- across 38 predicates and 4 regimes. Confluence is why it was
worth running: requiring two or three conditions to agree trades far less
often but more selectively, and trading less often is the only mechanism that
can lift a per-trade edge toward the cost line.

The filter was committed before the run, in `db82f61`, with the tiers in this
order deliberately:

    Tier 0  >= 200 non-overlapping trades
    Tier 1  gross edge per trade exceeds the round trip
    Tier 2  net edge beats the best-of-100 null
    Tier 3  net edge still positive out of sample
    Tier 4  still positive at taker cost

Tier 1 sits first because nothing downstream can rescue a strategy whose gross
edge does not clear its own costs. Ranking on win rate or Sharpe before that
question is settled is how a search produces a confident, worthless top five.

| | ETH | BTC |
|---|---|---|
| generated | 100 | 100 |
| produced trades | 66 | 67 |
| **Tier 0** (>=200 trades) | 53 | 59 |
| **Tier 1** (gross > 4bps) | **0** | **0** |
| Tier 2-4 | not reached | not reached |

Best gross edge among strategies with a measurable trade count: **+1.52bps**
(ETH, 22,851 trades) and **+2.18bps** (BTC, 3,327 trades). The round trip is
4bps all-maker and 14bps taker. Nothing came within half of the cheapest
possible cost.

This is consistent with, and independent of, the earlier tick measurement:
that put the intraday edge at ~0.5bps per trade at a two-minute horizon, real
at 4-6 sigma. Confluence and regime filtering do lift it -- roughly three to
four times -- and it is still not close. Three or four times too small instead
of thirty.

### Why no top five is reported

A top five was requested. Producing one would have meant ranking the survivors
of a filter that nothing reached, which is the exact failure this project has
already made four times. The leaders are listed by gross edge so the shape of
the result is visible, but every one of them loses money after costs, and the
ordering among them is noise.

One detail worth recording: the first version of this write-up would have
quoted BTC's best as **+24.08bps**. That was a 6-trade strategy -- precisely
what Tier 0 exists to exclude -- and it was being reported in a summary line
that ranked before the filter rather than after it. Fixed, and the ranking is
now taken from Tier 0 survivors only.

### Corrected: the first intraday run was measuring scalps

The run above tested 1m bars with a stop-loss grid reaching down to 10bps.
That was wrong, and the challenge that surfaced it was right: `hold` is only a
*maximum*, so a 10bps stop exits in seconds regardless of what the holding
parameter says. The evidence was in the output and went unread -- an 11.7% win
rate on a "240 minute" strategy, and 21,310 trades over 20 months, about 35 a
day. It was labelled intraday and measured scalping, because the geometry was
carried over from the earlier scalping work.

Corrected: 5m bars, stop-loss 50-200bps, take-profit 100-600bps, holds of
1-8 hours. Plus a diagnostic reporting the **actual** median holding time
rather than the configured ceiling, so the mislabel cannot recur silently.

| | 1m + scalping geometry | 5m + intraday geometry |
|---|---|---|
| actual median hold | seconds | **110 min** |
| trades per day | up to 35 | **2.6 (ETH) / 3.8 (BTC)** |
| Tier 1: gross > 4bps | 0/53 | **8/29 (ETH), 4/40 (BTC)** |
| best gross edge | +1.52bps | **+12.00 / +12.11bps** |

**At genuine intraday horizons the gross edge does clear costs.** That is a
real change from every previous result in this file, and it came from fixing
the timeframe rather than from finding a better signal. The old `tp150`
ceiling had appeared in four of the top seven -- a search pressed against its
own boundary, looking below where the answer lived.

It still fails, at Tier 2.

A second bug had to be fixed before that could be said honestly. The null was
being drawn from whichever geometry happened to be cached first and applied to
every strategy, which made the bar arbitrary in exactly the tier that rejects
everything. Per-trade dispersion depends heavily on tp/sl/hold, so each
strategy is now measured against a null built from its own geometry and its
own trade count.

That correction sharpened the result rather than softening it:

| strategy | trades | gross | its own null |
|---|---|---|---|
| ema5x30+ofi30 (ETH) | 206 | +12.00 | **+28.86** |
| break60 (BTC) | 319 | +12.11 | **+23.48** |
| mom60 (ETH) | 1001 | +9.68 | +11.29 |
| busy60+mom15 (ETH) | 432 | +8.04 | +14.27 |

The best-looking strategy on each instrument has the *highest* null, because a
few hundred trades at tp300/sl150 is a small, high-variance sample and
best-of-N selection produces +23 to +29bps there by luck alone. The single
flat threshold had been flattering exactly the strategies least able to
support the weight.

Every one of the twelve strategies that cleared costs falls short of what
random selection with its own trade count and geometry would have produced.
None survives at taker cost either -- all twelve need all-maker execution
merely to be positive before the null is considered.

## Pre-specified signals from the literature: both negative

Every search here has died in the same place -- a strategy clears its costs,
then fails to beat the best-of-N null for its own trade count. That penalty is
the price of *searching*. A signal specified in advance by someone else does
not pay it, so two were taken from the literature as published and tested
without tuning.

### Intraday momentum (Gao, Han, Li & Zhou, JFE 2018; Shen et al. 2022)

The first half-hour return of the day predicts the last half-hour return.
20 instruments, 18,860 day-observations.

| | published (SPY) | measured (crypto) |
|---|---|---|
| R^2 | 0.016 | **0.00203** |
| effect | slope +6.94, sig. at 1% | difference +0.73bps, **t = 1.79** |

The sign is consistent -- positive on 18 of 20 instruments -- but the effect is
roughly eight times weaker in R^2 than the equity original, and t = 1.79 is
below even the conventional 1.96, let alone the 3.4-3.8 the multiple-testing
literature requires. Net of a 4bps all-maker round trip it is **-1.09bps**.

This is what the stated prior expected. In equities the effect is attributed to
opening auctions and late-day portfolio rebalancing. Crypto trades continuously
with neither, and the UTC day boundary used here is a convention rather than a
market event. The correlation survives the move weakly; the mechanism does not.

One reporting correction worth recording: the first version of this test put
t = 4.89 on the raw mean of the position. That number was almost entirely
drift -- these instruments rise, so any long position shows a large t whether
or not the rule discriminates. Testing the *difference* between days following
a positive first window and days following a negative one gives t = 1.79, and
that is the statistic the claim actually makes.

### Short-horizon cross-sectional momentum

The crypto momentum literature places the effect at 1-4 week formation with
persistence limited to about a week, unlike the 12-month effect in equities.
The cross-sectional test run earlier in this project used 1, 3, 6 and 12 month
formation -- mostly outside that window, which was a real gap.

Tested at the window the literature points at, 402 weekly periods, 583
instruments, point-in-time with survivorship:

| formation | excess/week | t | positive weeks |
|---|---|---|---|
| 1 week | -0.654% | -1.96 | 41% |
| 2 weeks | -0.394% | -1.16 | 46% |
| 3 weeks | -0.392% | -1.15 | 44% |
| 4 weeks | -0.704% | -2.14 | 42% |

All four are negative, and the two endpoints approach significantly so. The
gap in the earlier test is now closed, and closing it did not help: at the
window the literature identifies, ranking on trailing return and holding the
top decile does worse than holding the universe, not better.

## Pooling across twenty instruments: the cleanest result in this file

The single-instrument search failed at the null, not at the cost gate, and the
diagnosis was statistical power: 206 trades at tp300/sl150 is a small,
high-variance sample where best-of-N selection produces +28bps by luck. The
remedy is more *trades*, not more strategies -- pooling one strategy across
twenty instruments raises n without raising the number of things selected over,
so the null falls as 1/sqrt(n) while a real edge does not.

20 instruments, 3.5M five-minute bars, same tiers, 75-minute median hold:

| | single instrument (ETH) | pooled (20) |
|---|---|---|
| trades | 206-1,856 | median **13,775**, max 154,984 |
| Tier 0 | 29/55 | 59/68 |
| **Tier 1: gross > 4bps** | 8/29 | **0/59** |

**Nothing clears costs once the sample is honest.** And the reason is the
single most useful number produced here:

| `mom60\|liquid\|tp400/sl50/240m` | gross edge |
|---|---|
| on ETH alone (1,001 trades) | **+9.68 bps** |
| pooled over 20 instruments (25,946 trades) | **+2.99 bps** |

The same strategy, the same geometry, the same period. The single-instrument
figure was roughly three times too optimistic, and pooling removed the
inflation rather than confirming it. That is precisely what the best-of-N null
had been warning about, now demonstrated directly instead of argued from
simulation.

Note what did *not* happen: the signal did not vanish. At 25,946 trades it
carries **t = 3.91**, which clears even the 3.4-3.8 hurdle the multiple-testing
literature demands. It is a real, statistically strong effect of **+2.99bps**
against a **4bps** floor on round-trip costs. Real, and too small -- the same
verdict reached at two-minute horizons (+0.5bps against 4bps), reached again
independently at 75 minutes with a hundred times the sample.

A display flaw was fixed here too: the top-5 table originally ranked over every
strategy rather than over Tier 0 passers, which put a one-trade, 100%-win
artifact (+63.47bps, PROMUSDT, n=1) at the top. That is exactly the impression
the filter exists to prevent, and it should not have been printed above the
real rows.

## There are not twenty independent crypto assets

The pooled result was reported with **t = 3.91** across 25,946 trades, and that
number was wrong. It treated every pooled trade as an independent observation.

Measured at hourly horizons across the twenty instruments used:

| | |
|---|---|
| mean pairwise correlation | **0.563** |
| effective independent instruments | **1.7 of 20** |
| pooled sample worth | **~9%** of its trade count |

Correcting for that puts the strategy nearer **t = 1.2**, not 3.91 -- below the
conventional 1.96, never mind the 3.4-3.8 the multiple-testing literature asks
for. The claim that it "clears even the multiple-testing hurdle" was false.

The instrument set was chosen by volume, and volume in crypto concentrates in
Layer 1 blockchains: fourteen of the twenty were L1s. So the obvious remedy was
category diversity. It does almost nothing.

| set | mean correlation | effective independent |
|---|---|---|
| 20 names, L1-heavy | 0.563 | 1.7 of 20 |
| 10 names spanning distinct categories | 0.469 | **1.8 of 6 measured so far** |

Deliberately spanning memecoin, exchange token, oracle, lending, DEX, L2 and
privacy moved the correlation from 0.56 to 0.47 and effective breadth from 1.7
to 1.8. The per-name detail explains why:

| instrument | category | correlation with BTC |
|---|---|---|
| LTC | privacy / old L1 | +0.666 |
| DOGE | memecoin | +0.636 |
| LINK | oracle | +0.619 |
| BNB | exchange token | +0.608 |
| **PAXG** | **tokenised gold** | **+0.141** |

A memecoin, an oracle token and an exchange token all move with Bitcoin at
about 0.62. **Category labels in crypto describe what a token claims to do, not
what drives its price.** At intraday horizons the whole asset class is close to
one trade with different volatility multipliers.

The exception proves the rule: PAXG is a claim on physical gold, the only
instrument here with an anchor outside crypto, and the only one that
diversifies.

This reframes the pooling strategy rather than refining it. Statistical power
in crypto cannot be bought by adding names, because the names are not
independent -- there are roughly two effective assets available, and one of
them is gold. Every pooled figure in this file, including the ones reported as
improvements, has to be read against an effective sample about a tenth of its
nominal trade count.

`multi_instrument_search.py` now clusters trades by day before computing
significance, so correlated instruments cannot inflate a t-statistic again.

## The diverse set: one strategy cleared the null, then died out of sample

Running the same search over ten category-diverse instruments rather than
twenty L1-heavy ones changed the picture at Tier 1 -- 5 of 57 cleared costs
against 0 of 59 before -- and for the first time in this project something
cleared the best-of-N null.

`low30|vol_low|tp300/sl100/240m`, held 240 minutes, entered on a 30-bar low in
a low-volatility regime:

| | train (2024-01 to 2025-08) | test (2025-09 to 2026-07) |
|---|---|---|
| pooled trades | 8,061 | 4,680 |
| gross | **+4.59 bps** | **-1.00 bps** |
| t, day-clustered | **+4.58** | +1.29 |
| positive on | **10 of 10 instruments** | 4 of 10 |
| its own best-of-N null | +3.76 (cleared) | -- |

In sample it passed every check available: a cluster-robust t of 4.58 that
respects the 0.54 cross-correlation, an edge above its own best-of-N null, and
a positive result on **every single instrument**. That is a stronger in-sample
case than anything else produced here.

Out of sample it is negative, and positive on fewer than half the instruments.

This is the clearest demonstration in the file of why the held-out tier is not
optional. Every in-sample guard -- the cost gate, the clustered t-statistic,
the per-strategy null, consistency across ten instruments -- passed. None of
them detected that the effect would not survive the next eleven months. Only
running it on data the selection never touched did that.

Worth noting what category diversity actually bought. It did not raise
statistical power: effective breadth is 1.7 whether the set is ten diverse
names or twenty L1s. What it changed was *which* strategies became reachable,
because PAXG and FET behave differently enough to admit rules the L1-only set
never triggered. That is a genuine effect, and it still was not enough.

The per-instrument breakdown of the runner-up says the same thing from another
angle: `low300|any|tp600/sl200/240m` scores +8.63bps pooled, but +36.39 of that
comes from FET on 249 trades while LINK and ARB are negative. Positive on 6 of
10. A pooled average can be carried by one name, which is why the breakdown is
printed rather than summarised.

## The mid-cap band: the best result here, and it is one memecoin

The move-to-cost ratio favours mid-caps -- 10.3x in the $20-100M daily volume
band against 7.7x for the majors -- so the search was re-run there on 17 names,
with liquidity measured on the training window only so selection could not peek
at the test period, and with MATIC, FTM and RNDR included despite having
delisted mid-period.

It produced the strongest tier progression in this file:

| tier | 20 L1-heavy | 10 diverse | **17 mid-cap** |
|---|---|---|---|
| Tier 1 gross > cost | 0/59 | 5/57 | **12/59** |
| Tier 2 beats own null | 0 | 1 | **3/12** |
| Tier 3 holds out of sample | -- | 0/1 | **2/3** |
| Tier 4 survives taker cost | -- | -- | **0/2** |

Two strategies survived out-of-sample testing, which nothing had managed
before. Then the per-instrument breakdown settled it:

| strategy | PEPE's share of the edge | from % of trades | edge without PEPE |
|---|---|---|---|
| low30+ofi10 | 75% | 18% | +15.78 -> **+4.76** |
| low60+ofineg30 | **100%** | 9% | +5.35 -> **+0.02** |
| low60+ofineg10 | 47% | 9% | +9.22 -> **+5.41** |

`low60+ofineg30` is the clean illustration. It has the strongest out-of-sample
t-statistic of the three at +3.45, and without PEPE its edge is **0.02bps**. It
is not a strategy; it is a long position in one memecoin during an
extraordinary run, wearing an entry rule as a disguise.

Nothing survives taker cost. The best case after removing PEPE is
`low60+ofineg10` at +5.41bps gross, **+1.41bps net** at all-maker execution,
with an out-of-sample t of 1.95 -- below significance.

This is a different failure from the earlier ones, and worth naming. The
majors failed because no edge existed above costs. The mid-caps fail because
the edge that does exist is **concentrated in a single instrument**, and a
pooled average conceals that unless the breakdown is printed. A strategy
positive on 12 of 17 names still had three quarters of its return from one of
them.

A survivorship bug was fixed to get here. The pooling code required an
instrument to have *both* training and test data, which silently dropped every
name that delisted between the two -- reintroducing exactly the bias the
point-in-time universe exists to remove, in the band where delisting is most
common. Train and test membership are now decided independently.

## The component hierarchy, tested as gates

A proposed system combining market structure, liquidity sweeps, volume
profile, order flow, footprint, CVD/delta, VWAP and volatility, weighted
20/20/15/20/10/10/5.

Two things were changed before testing, both for stated reasons.

**Gates rather than weights.** Seven weights are seven fitted parameters, and
this project has already produced a strategy that reached a day-clustered t of
4.58, beat its own null, was positive on 10 of 10 instruments and then lost
money over the following eleven months. As AND-gates the hierarchy has no free
parameters, so any result is not a tuning artifact.

**The weighting assumed an independence the data denies.** Measured on ETH 5m
over 20 months:

| pair | correlation |
|---|---|
| market structure vs VWAP deviation | **0.72** |
| market structure vs CVD slope | 0.56 |
| CVD vs VWAP deviation | 0.47 |
| OFI vs CVD | 0.45 |

Structure, VWAP, CVD, delta and order flow are one directional factor observed
at different granularities. Only volatility and volume are independent of it.
Seven components carry about **3.2 independent dimensions**, so a scheme
placing 85% of its weight on the directional cluster feels far more confirmed
than it is. That said, 3.2 of 7 is much better than instruments manage (1.7 of
20) -- combining indicators genuinely does add more information than adding
coins.

### Result: no gate beats entering unconditionally

Each gate alone, 2.1M bars across 12 mid-cap instruments, tp150/sl100/240m:

| gate | % of bars | trades | gross bps |
|---|---|---|---|
| *(unfiltered)* | 100% | 117,557 | **-0.65** |
| 1 direction | 44.8% | 60,494 | **-1.56** |
| 2 location | 49.8% | 67,551 | -1.02 |
| 3 sweep | 3.3% | 27,106 | **-1.78** |
| 4 participation | 37.0% | 76,438 | -0.84 |
| 5 pressure | 45.1% | 78,926 | **-1.81** |
| 6 worth it | 99.9% | 117,557 | -0.65 |

Every filter returns **less** than no filter, on samples large enough to mean
it -- 27,000 trades on the rarest gate. Cumulatively the stack collapses to 41
trades by gate 3, because the gates **conflict**: an uptrend, a price below
VWAP and a swept five-hour low rarely coincide, since the first is bullish
structure and the other two are weakness. Stacking them does not concentrate
signal, it finds the few bars where contradictory conditions happen to meet.

### What this does and does not establish

It tests a mechanical encoding of these concepts, not a discretionary trader's
reading of them. "Market structure" here is an EMA relationship, not
higher-highs-and-higher-lows; "liquidity sweep" is one interpretation of many.
A human applying the same vocabulary may mean something this does not capture.

Two components could not be tested at all: **footprint and order-book
imbalance need per-price-level bid/ask volume and L2 depth**, which the kline
archive does not contain. Testing them requires collecting L2 snapshots
prospectively -- months of it -- or buying the data.

What is tested is well-proxied: VWAP directly, order flow and CVD through the
taker-buy split, volatility and volume directly. Those four are the measurable
core of the design, and all four are negative.

## The final crypto test: negative

Run against `docs/CROSS_SECTIONAL_V2_PREREGISTRATION.md`, committed before the
script existed. This was the one avenue left open: the v1 cross-sectional test
held 3-name portfolios and could not distinguish "no edge" from "an edge too
small to see". v2 fixes the power problem -- full universe (median 292 rather
than 37), 20% slice with a 15-name floor, a 30-name floor on scoring a period.

| cell | best signal | excess/period | null | verdict |
|---|---|---|---|---|
| **full, 30d** (primary) | mom_1m | +1.999% | +2.319% | FAIL |
| **full, 90d** (primary) | low_downside_vol | +3.708% | +11.742% | FAIL |
| unflagged, 30d (secondary) | low_downside_vol | +1.672% | +1.437% | **passed all four** |
| unflagged, 90d (secondary) | low_downside_vol | +1.103% | +5.945% | FAIL |

**The primary test failed on both horizons.** One cell met all four criteria,
and it is the secondary universe -- the one the pre-registration described in
advance as "strictly less powerful than the primary, so if the primary fails
the secondary cannot rescue it; it is reported for completeness, not as a
second chance."

That sentence was written before the run, and it decides this.

### Why the pre-registration was right to say so

Three diagnostics, each independently sufficient:

**The sign flips.** `low_downside_vol` scores +1.672% per period in the
unflagged universe and **-0.595%** in the full universe -- same signal, same
30-day horizon, same period. An effect that reverses when the universe widens
is not an effect.

**It is a bear-market artifact.** The equal-weight benchmark fell **84.6%**
over the period. Splitting the excess by benchmark direction:

| | periods | excess |
|---|---|---|
| benchmark rose | 22 | +0.280% |
| benchmark fell | 37 | **+2.499%** |

Nearly all of it is losing less during a collapse. In rising periods the edge
is 0.28% per month, which is noise. Beta is 0.99, so this is not simple
de-risking -- it is avoiding the specific names that blew up, in a sample
dominated by blow-ups.

**Multiplicity.** Four cells were tested at a 95% threshold, so the chance at
least one passes by luck is about 19%. Exactly one did.

### What this closes

Crypto is answered. Eight approaches now: seven intraday and cross-sectional
searches, plus this properly-powered final test. The caveat that motivated v2 --
that v1 might have missed a real effect through lack of power -- is resolved.
Power was added, the primary test still failed, and the one cell that passed
fails every robustness check applied to it.

Per the pre-registration, the search stops here.

The result is also a demonstration of why the discipline is worth its cost. A
"PASS on all four criteria" appeared on screen, in a project that has spent
months looking for exactly that. Without criteria fixed in advance -- and
without the sentence saying the secondary cannot rescue the primary -- it would
have been entirely natural to write it up as a defensive-factor discovery, with
a plausible story about downside protection attached.

## CTREND: the one literature claim that contradicted our cost measurement

A survey of the crypto cross-section literature turned up one result that did
not fit. Every documented anomaly draws its alpha from micro-caps -- size,
volume and distress anomalies take their returns from coins representing under
**0.3% of aggregate market capitalisation and 0.5% of volume**, which sits
below the liquidity band where our own measurement puts the move-to-cost ratio
at 1.3x. The exception was CTREND (*A Trend Factor for the Cross Section of
Cryptocurrency Returns*, JFQA), reported to survive transaction costs and to
persist **in big and liquid coins**.

That is the one claim worth testing, because it contradicts our own arithmetic
rather than confirming it.

CTREND is also different in kind from the ~400 variants already tested here.
Those were fixed rules. This is a *learned* factor: moving averages over many
horizons combined by coefficients estimated from the cross-section, following
Han, Zhou and Zhu (2016). Implemented with 7 price and 7 volume horizons, and
weights taken as the mean of past cross-sectional regressions -- expanding
window, so the weights at any rebalance date come only from periods that had
already resolved.

| universe | rebalance | excess/period | null | Sharpe | folds |
|---|---|---|---|---|---|
| full | 7d | -0.016% | +0.549% | -0.01 | 1/5 |
| full | 30d | +2.460% | +2.680% | 0.45 | 1/5 |
| **liquid50** | 7d | +0.001% | +0.086% | 0.00 | 3/5 |
| **liquid50** | 30d | -1.043% | +0.639% | -0.46 | 3/5 |

**Zero of four cells met the criteria**, and the paper's own claim -- big and
liquid coins -- is where it performs worst: +0.001% weekly on the top-50 liquid
subset, and -1.043% monthly. The closest approach is full-universe monthly at
+2.460% against a null of +2.680%, which does not clear its own null and is
positive in 1 of 5 folds.

The factor had every honest advantage: 14 signals, 343 weekly periods, and
weights free to adapt to whichever horizons predicted. It still does not beat
picking at random from the same universe.

One caveat recorded: the paper aggregates with machine learning and this uses
the regression construction it builds on. A better learner might combine the
same 14 signals more effectively -- but it would have to find something the
linear combination missed *and* clear a null that already prices in the search,
using the same price and volume series that failed every prior test.

### What this closes

It removes the last inconsistency between the literature and our own
measurements. The two now agree completely: **the documented alpha lives in
coins too small to trade.** Our cost work found the move-to-cost ratio peaking
at $20-100M daily volume and collapsing to 1.3x below $200k; the literature
places the alpha below that line. Two independent lines of evidence, same
boundary.
