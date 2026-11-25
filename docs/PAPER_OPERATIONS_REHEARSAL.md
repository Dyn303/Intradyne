# Phase 3 paper operations rehearsal

## Deployment under test

The supported Phase 3 profile is Compose with Caddy, one API worker and PostgreSQL 16. The Compose profile forces production authentication, paper mode, and an idle strategy loop while the research gate is closed. Paper orders entered through the API still use the shared execution path. The isolated rehearsal used loopback bindings, disposable credentials and storage, and a local alert receiver. The external ACME issuance and third-party paging account were outside the rehearsal.

## Authenticated proxy smoke log

| Surface | Observed result |
| --- | --- |
| API authentication | Anonymous risk status returned 401. Authenticated risk, readiness, portfolio and research routes responded. Separate operator authorization was required for runtime parameter changes. |
| Dashboard and API contract | Caddy served the dashboard. Authenticated research returned its record index. The live OpenAPI document exposed the order, risk and parameter apply routes tested through Caddy. |
| CORS | The configured origin passed preflight. A different origin returned 400 without an allow-origin header. |
| Reverse proxy | Caddy returned 404 for `/metrics`, even with an API key. Internal Prometheus reported its sole `api:8000` scrape target up. Caddy, Prometheus, and Alertmanager accepted their production-profile configuration. |
| Grafana | Loopback health passed. Anonymous datasource access returned 401. Authenticated inspection confirmed the datasource points to internal Prometheus. |
| TLS | A separate loopback Caddy instance served authenticated readiness over HTTPS, with hostname and certificate validation against its disposable CA. |
| Paper flow | An authenticated paper order filled and added BTC inventory. An operator halt blocked the next order. Ordinary API credentials could not apply parameters, and authorized apply returned 409 with the strategy loop disabled. |
| Diagnostics | The authenticated network diagnostic reached PostgreSQL over the private Compose network. |

## Candidate control

The n8n definition is inactive, manual, and read-only. It has no scheduled optimization, score comparison, or apply node. Candidate files and the production artifact are distinct. Production rejects legacy research activation even with operator authorization. A reviewed release image is the promotion mechanism for this profile.

## Alert and outage evidence

Prometheus evaluated the `Trading_Halted` rule after an authenticated paper halt. Alertmanager delivered `Trading_Halted firing` to the local webhook, whose acknowledgement was `ack Trading_Halted firing` and HTTP 200. The rehearsal recorded receipt before continuing recovery.

Stopping the isolated PostgreSQL service made authenticated readiness return HTTP 503 through Caddy. Starting PostgreSQL restored readiness. The regression suite exercises a simulated feed drop and supervised restart, missing marks, and unresolved broker claims. The Compose paper rehearsal uses no venue account or live feed.

## Backup, restore, and image

The rehearsal stopped the only API writer, exported a custom-format PostgreSQL dump, and copied its ledger. It restored the dump into a new database, recreated the API against the restored database and ledger, and verified readiness through Caddy. The bytes of the restored ledger matched the backup exactly. The restored halt remained engaged until the authorized release succeeded.

| Evidence | SHA-256 |
| --- | --- |
| PostgreSQL rehearsal dump | `a8e48eb074736cb1abf87799b0acb57a08d9fe2add11a17c2dd6e2802fd5205a` |
| Rehearsal ledger | `84273320d2fe0f64deb51f5a190047b082c7cb84dfd2f8091fbf3eacb15a003a` |
| Final API image manifest | `c98354d9ce370bb16ea4efdbe4751f031ea206eb08ada6d22ef7c8d83f6a72fe` |

The final API image is 174,296,603 bytes. The application tags recorded for the Compose and Helm definitions are both `1.9.0`. Only the Compose and Caddy profile was exercised here.

## Regression verification

The full suite passed 1,046 tests with PostgreSQL 16 enabled, including signed Telegram Mini App permissions, candidate promotion denial, dashboard behavior, engine supervision, and order reconciliation. Ruff lint and format, mypy, and the research ledger check passed. Starlette emitted one AnyIO deprecation warning. The built container reported healthy against authenticated `/readyz`.

See the [operations runbook](../RUNBOOK.md) for versioned startup, halt, backup, restore, and image rollback instructions.
