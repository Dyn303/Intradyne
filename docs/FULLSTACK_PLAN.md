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
