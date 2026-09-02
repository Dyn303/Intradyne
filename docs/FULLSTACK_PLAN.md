# Full-stack plan

The backend is already there: 32 endpoints across 9 route modules, an
authenticated WebSocket at `/ws/ticks`, CORS that refuses a bare `*` in
production, Prometheus metrics, and four compose files. What is missing is a
frontend — `frontend-sdk/` holds the OpenAPI codegen tooling and no app.

So "full stack" here means one thing: **build the web client, and package it so
the whole system starts with one command.**

## What this is and is not for

The engine refuses to start without a demonstrated edge, and ten approaches
established there isn't one. So this dashboard will mostly show a system
correctly declining to trade.

That is a legitimate thing to build anyway. It completes the system as an
engineering artifact, it is bounded work with a clear finish line, and it does
not depend on research that has already concluded. What it is **not** is a step
toward trading — nothing here changes the edge arithmetic.

Worth being explicit because this project's recurring failure mode has been
scope creep back into strategy search.

## The decision that shapes everything: local-only or exposed

**Recommended: local-only.** The API authenticates with an `X-API-Key` header.
A browser SPA has to hold that key, and a key in browser storage is readable by
any script on the page. On `localhost` that is acceptable — the only thing that
can read it is you. Exposed to the internet it is not, and fixing it properly
means adding a session layer, CSRF handling and probably OAuth, which is
several times the work of the dashboard itself.

Local-only also matches the constraints: no server, a laptop, intermittent
connectivity.

If it ever needs to be reachable remotely, the honest path is a tunnel
(Tailscale, Cloudflare Access) in front of it rather than building auth. That
keeps the security boundary somewhere audited.

## Stack

| choice | why |
|---|---|
| TypeScript | `openapi-typescript` is already wired up; types come from the live spec |
| Vite + React | the boring default; fast rebuilds, no framework surprises |
| TanStack Query | polling, caching and refetch are most of what this app does |
| Recharts | one chart type (equity curve), not worth a heavier library |
| No CSS framework | ~6 screens; hand-written CSS is smaller than configuring Tailwind |

The client is generated from the running server's OpenAPI, so an endpoint
signature change breaks the build rather than the page.

## Phases

Each phase ends somewhere shippable. Stop after any of them and the result is
still coherent.

### Phase 1 — read-only dashboard

The proof that the stack works end to end.

- `/healthz`, `/readyz`, `/version` → a status strip
- `/engine/status`, `/engine/state` → running or gated, and why
- `/portfolio`, `/overview` → positions and balances
- `/risk/metrics`, `/risk/status` → drawdown, VaR, kill-switch state
- `/ledger/tail` → recent events, with the hash chain shown as verified

Deliverable: `npm run dev` shows live system state. No writes.

### Phase 2 — controls

Everything here changes system state, so every action needs a confirmation step
and lands in the ledger.

- `POST /admin/halt` and the kill-switch toggle
- `POST /engine/params/apply` and `/revert`
- Display the current gate reason prominently — a user should never wonder why
  the engine is not trading

Deliverable: the system can be operated from the browser.

### Phase 3 — research views

Renders what already exists as JSON rather than computing anything new.

- `docs/universe_timeline.json` → universe size over time, delisted names
- `docs/universe_candidates.json` → the screening worksheet as a filterable table
- `artifacts/*.json` → edge reports, the tiered filter results, CTREND
- `/research/*` endpoints for backtests

Deliverable: the research record is browsable instead of living in markdown.

### Phase 4 — packaging

- Build to static assets, served by FastAPI `StaticFiles` at `/`
- One `docker compose up` starts API and UI together
- Extend `scripts/e2e_smoke.py` to check the UI is served and the API answers

Deliverable: `docker compose up` gives a working system at `localhost:8000`.

## What I would deliberately leave out

- **Live tick charting.** `/ws/ticks` exists, but a candle chart is a large
  amount of work to display data the strategy does not use.
- **Mobile layout.** A single-user local dashboard on a laptop.
- **Multi-user, roles, accounts.** There is one user.
- **A trade-entry form.** Orders should come from the engine, which is gated.
  A manual order form is a way to bypass the risk checks by accident.

## Risks

**Scope creep back into research.** The most likely failure. The dashboard
makes it easy to look at results and want to re-run them. The memory files say
crypto is closed; that does not change because the results now have a nicer
view.

**The API key in the browser.** Acceptable at localhost, not beyond it. If the
scope changes to remote access, stop and add a tunnel rather than rolling auth.

**Node toolchain drift.** `openapi-typescript` had 12 advisories via `undici`
when this project last checked. Pin versions and keep the lockfile committed;
CI already runs a secrets scan but does not audit npm.

## Effort

Phase 1 is the bulk of the setup and perhaps a day. Phases 2 and 3 are a few
hours each on top. Phase 4 is under an hour. None of it is research.

---

## Phase 1: built, with one deviation

Delivered as a **single self-contained HTML file** served by FastAPI, not the
Vite + React + TypeScript app this plan originally specified. The reasons,
recorded because the deviation is deliberate:

- Five read-only polling panels for one user. React earns nothing at this size.
- Phase 3 is a Telegram Mini App, which is a web page. A single file is the
  ideal shape for that, and the work transfers with no rewrite.
- A PR in this project removed twelve `undici` advisories from the only npm
  dependency here. Adding ~200 packages back for five panels is a poor trade.
- A zero-install page works on a connection that drops regularly.

The API contract is unchanged, so React can replace this later if the
dashboard ever outgrows it.

### What it shows

A status strip (version, mode, engine state, connection), then panels for
engine, risk, portfolio, health, and the ledger tail. Risk values are drawn as
bars against their own thresholds, because "0.4%" does not convey how close to
a 20% halt limit it is and a bar does.

The most prominent element is the gate banner: when the engine is not running
it says so and gives the reason. A user should never have to wonder why nothing
is happening.

### Two bugs found by running it

**`/metrics` returned 404.** The mount at `"/"` was inside `create_app`, but
`/metrics` and `/frontend/config` are declared afterwards at module level, and
Starlette matches routes in registration order -- so the mount shadowed them.
This is the second time `/metrics` has been silently hidden in this project.
`mount_dashboard()` is now called at the end of the module, and
`test_dashboard_mount.py` asserts ten API routes still answer.

**The page scrolled sideways on a narrow viewport.** `minmax(330px, 1fr)`
forces a 330px column even when the viewport is 335px. Fixed with
`minmax(min(330px, 100%), 1fr)`. Worth catching now: Phase 3 runs at phone
widths.

### Running it

    PYTHONPATH=src ENGINE_ENABLED=false API_AUTH_REQUIRED=0 \
      python -m uvicorn intradyne.api.app:app --port 8011

Then open `http://localhost:8011`. With auth enabled, pass the key as `?key=`.

---

## Phase 3, done early: the Mini App

Phase 3 was research views. The Mini App got built first because it changes
the security model, and doing that before Phase 2 adds write controls is the
cheaper order.

The dashboard needed no rewrite -- a Mini App is a web page, and choosing a
single self-contained file over a React app in Phase 1 turned out to be exactly
the right shape. What was added is `api/telegram_auth.py`: Telegram signs an
`initData` payload with the bot credential, the page forwards it, the server
verifies it. The credential never reaches the browser, which answers the
objection this plan raised against exposing the API at all.

It only answers half of it. **A Mini App cannot be local-only** -- Telegram
will not open localhost, so the URL must be public HTTPS. A valid signature
then proves only that the request came from *a* Telegram user, any of them. So
`TELEGRAM_ALLOWED_USER_IDS` is mandatory, and an unset allowlist disables Mini
App auth entirely rather than defaulting to open: a configured bot with no
allowlist is the genuinely dangerous state, because it verifies signatures
perfectly and admits everyone.

The tunnel recommendation in this plan still stands and is now required rather
than optional. See `TELEGRAM_MINI_APP.md`.

This does not change the "what this is and is not for" section above. The
dashboard still mostly shows a system correctly declining to trade; it can now
do so from a phone.

---

## Phase 2: controls, and two endpoints that were lying

The UI half was small. The work was in the endpoints behind it, because two of
the three controls this phase was meant to expose did not do what their names
said -- and a control that reports success without acting is worse than no
control, since the operator stops looking.

**`/admin/kill-switch/toggle` could not stop trading.** It appended a ledger
line and returned `{"ok": true}`. Its own comment called it a placeholder and
deferred enforcement to "breach count" -- but that is the *automatic* kill
switch inside `Guardrails`, a threshold of N breaches in 24h with no on/off
control. So the endpoint named kill-switch was the only one in the system that
could not halt anything, and it reported success for doing so. It now moves the
same halt that `Guardrails.gate_trade` and the broker consult.

**`/engine/params/revert` never reverted.** `apply` copied
`production_params.json` into the `.prev` backup *before* applying it -- but
that file already held the new values, so the backup was a copy of what was
being applied rather than of what it replaced. Revert then re-applied the
current configuration and returned `{"reverted": true}`. The engine cannot be
asked what it is running, so the applied configuration is now tracked in
`production_params.applied.json` and the backup is taken from that. One level
of undo, and the backup is consumed so a second revert says so rather than
flip-flopping.

### The asymmetry in the UI

Halt and Resume are never both on screen: one switch, one direction, nothing to
misread under pressure. Stop is full-width and red; start is not. The
confirmation on Halt is a formality against mis-taps, while the one on Resume
actually warns, because the two directions are not equally safe -- halting a
healthy system costs idle minutes, resuming one that should be stopped costs
money.

Inside Telegram the confirmations use the native dialog and success fires a
haptic tap.

### Known rough edge

If `ADMIN_SECRET` is set, `/admin/halt` needs a header the browser cannot hold,
so the Mini App cannot halt and the panel says so on the 401 rather than
failing silently. Leave it unset when using the Mini App: the router already
sits behind API auth plus the Telegram allowlist, which is a stronger gate than
a shared secret.
