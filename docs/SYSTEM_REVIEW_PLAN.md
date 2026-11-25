# Intradyne system review and implementation plan

## Primary goal

Make Intradyne’s research results reproducible and its spot-only operator workflow safe to run, stop, recover, and audit. Establish the evidence required for each research, paper, and live decision.

## Current baseline

Reviewed the local `master` at `18f9d5e`, which matched the local `origin/master` tracking ref. GitHub returned no combined status entries for that SHA. Local Python starts through `.venv` but lacks pytest, Ruff, and mypy. Docker Desktop’s Linux engine was unavailable. Findings below are a source review except where a separate observation is identified.

The cloud task independently reported that Alpha Vantage was reachable but returned an error and no weekly prices. It saw the key declared in a draft and absent from the running task’s active secret bindings. Its last reported weekly score was 80/106 departed holdings, with five more needed for 80%, and all 24 completed filing windows passing. The cited cloud commit `e7c00bc` is unavailable in the local Git object store. Local `scripts/recover_delisted.py` evaluates daily coverage, so the cloud number cannot serve as a local benchmark until the revisions, data, and coverage definitions are reconciled.

| Area | Current implementation and evidence | Priority finding |
| --- | --- | --- |
| Runtime and compliance | `api/app.py:59`, `api/deps.py:256`, `engine/execution.py:105,329` under `src/intradyne` share execution between API and strategy. `risk/guardrails.py:226–337` applies the whitelist, drawdown, breach, exposure, flash-crash and VaR checks. | P0. Manual halt is process-local (`risk/kill_switch.py:1–42`) and automatic halt is instance-local (`risk/guardrails.py:214,245`). The router has a separate flash rule (`engine/risk.py:89–103`). Live boot is hard-gated (`core/config.py:454–487`). |
| State, execution and ledger | SQL order idempotency and startup reconciliation exist (`core/idempotency.py:92–146`, `engine/reconcile.py:32–46`). SQLite and PostgreSQL share backend adapters (`core/db.py:184,312`). The ledger uses hash-chained JSONL with a process-local writer lock (`core/ledger.py:13–32,131`). | P0. Demonstrate restart-safe halt, reconciliation after ambiguous broker responses, ledger integrity, and a single writer before operator rollout. |
| Research and market data | `research/spus_panel.py:127–250` models point-in-time SPUS membership and coverage. `scripts/recover_delisted.py:189,314,577` distinguishes a daily coverage gate from span estimates. `engine/data_loader.py:122–175` can synthesize sub-minute bars from a minute cache. `engine/optimize.py:102–203` searches a configured window and writes a candidate. | P0 for provenance and coverage, P2 for research runs. A weekly observation requires a verified identity and weekly window. Native tick provenance and an independent holdout are required for qualifying sub-minute evidence. |
| Operator surfaces and integrations | FastAPI routes and a bundled dashboard (`api/app.py:103–198`, `api/static/index.html:338–403,538–613`), Telegram HMAC auth (`api/telegram_auth.py:88–142`), and an OpenAPI generator (`frontend-sdk/package.json`). | P0. The two operator halt routes apply different authorization checks (`api/routes/admin.py:12–52`). The checked-in n8n tuning workflow goes from optimization to parameter apply (`deploy/n8n/workflows/nightly_tuning_apply.json`). |
| Deployment and operations | Docker Compose, Caddy, Helm, Prometheus and alerts are checked in. CI builds and smoke-tests an engine-disabled SQLite container and separately exercises PostgreSQL (`.github/workflows/ci.yml:17–41,61–115`). | P1. Exercise the actual production profile. Verify explicit production auth, dashboard/research assets in the image, version parity, metrics exposure, alert delivery, backup and restore. Compose and Helm specify different versions. |

## Phase 0 — Reconcile the baseline and establish test tooling

**How this serves the primary goal:** Every safety and research claim can be reproduced against a named revision and environment.

See [Phase 0 baseline evidence](SYSTEM_BASELINE_PHASE_0.md) for the revision matrix, data inventories and gate results.

- Capture local, remote and cloud SHAs, the cloud weekly coverage report and patch, and cache manifests with source, frequency, cutoff and checksums. Compare the cloud durable-halt claim with the local halt paths. Review differences in an isolated branch before selectively integrating verified changes.
- Use `.python-version`, `requirements-dev.txt` and `.github/workflows/ci.yml` as the toolchain contract. Run Ruff lint and format checks, mypy, pytest with PostgreSQL enabled, the research ledger freshness check, and Docker build and image smoke. Capture the test SHA, database variants, image digest and size. Resolve tooling and infrastructure gaps before recording a green baseline.
- Reuse `OrderKeyStore` and `reconcile_on_start` for SQL durability patterns, `PriceSource` and the SPUS panel for as-of data selection, `scripts/research_ledger.py:98–162` for provenance, and `scripts/e2e_smoke.py:62–72` for surface checks. Confirm exact signatures in the reconciled revision before editing.
- **Exit evidence.** A commit and environment matrix, two clearly labeled coverage baselines or a reproduced common baseline, plus test logs that show which gates actually ran.

## Phase 1 — Verify halt, order safety and audit recovery

**How this serves the primary goal:** Operators can stop exposure and recover safely after a restart, a retry, or an ambiguous order response.

- Trace manual and automated orders through their shared execution manager. Verify spot-only instrument mapping, whitelist denial, 30-day drawdown warning at 15% and halt at 20%, strict flash pause above 30% in one hour, halt on three breaches in 24 hours, and VaR step-down above 5%. Exercise stale or missing marks and separate new exposure from authorized risk-reducing exits.
- Adopt a durable halt store or verify the cloud implementation before porting it. Restore state before readiness and order admission. Exercise concurrent halt and submission, scheduled restart, SQLite and PostgreSQL, ambiguous broker acknowledgement, and manual release authorization across both admin routes and Telegram. Follow `tests/test_halt_and_live_gate.py`, `tests/test_order_path.py`, and `tests/test_live_readiness.py`.
- Prove one authoritative execution and ledger writer for the supported topology. Verify hash-chain continuity across restart and restore, protection from concurrent writes, and safe readiness behavior on corrupt state. Use `tests/test_ledger_chain.py`, `tests/test_db_backends.py`, and `RUNBOOK.md:53–94`.
- **Exit evidence.** A passing safety matrix for both order entry points, restart and concurrency tests in both databases, release authorization tests, and an operator stop and restore rehearsal. Preserve the hard live gate through these phases.

Phase 1 proof is recorded in [the operations runbook](../RUNBOOK.md#4-halting-trading). `tests/test_order_path.py`, `tests/test_engine_tick_path.py`, `tests/test_guardrails_active.py`, and `tests/test_flash_crash_exits.py` exercise the shared order and risk paths. `tests/test_admin_and_ready.py`, `tests/test_telegram_auth.py`, `tests/test_live_readiness.py`, `tests/test_db_backends.py`, `tests/test_paper_fills.py`, and `tests/test_ledger_chain.py` cover the new stop, authorization, reconciliation, and audit behavior. The PostgreSQL-enabled run passed 1,041 tests, followed by a 13-test exact-threshold suite and a focused unavailable-store check. The paper image passed all 10 smoke checks and the stop and restore rehearsal. Local image digest is `sha256:2047067f7e8d19cfe6702b28c9080aa5c5ec46aa0707e153dfd92ecd6a562586` (174,295,147 bytes).

## Phase 2 — Reproduce data coverage and qualify research evidence

**How this serves the primary goal:** Research reaches strategy evaluation only when the underlying history, timing and execution assumptions stand up to review.

- Verify the HTTP secret name and exact HTTPS destination on a newly started cloud instance. Classify response payloads and validate a listed symbol control and a known departed symbol’s required weekly span. Use `scripts/av_tier_probe.py:89–105,170–217` for response classification. If access or provider coverage fails, use equivalent historical files with provenance or retain the research block. Keep raw credentials and credential-bearing URLs out of artifacts.
- Rebuild point-in-time membership using filing effective dates and documented corporate-action mappings. Record weekly sample expectations, unresolved identities, qualifying and missing windows, and exclusion reasons. Reproduce the reported counts before using them to accept the 80% departed-name and per-window coverage checks. Keep daily and weekly measurement separate. Follow `research/spus_panel.py`, `scripts/recover_delisted.py:314–329`, and `docs/EQUITY_PROGRAMME_STOP_RULE.md:149–205`.
- For eligible research only, preregister costs, liquidity, dates, controls, concentration, statistical power and acceptance criteria. Include an independent chronological holdout and the applicable stop rules. For sub-minute crypto, record native Tardis provenance and explicitly reject minute-derived bars for qualification. Keep optimization and candidate production separate from promotion. Use `scripts/sweep_edge.py:143–209`, `scripts/download_tardis.py:81–141`, and `docs/STRATEGY_RESEARCH_FRAMEWORK.md:524–564`. Apply the existing closure and reopening rules in `docs/CRYPTO_REOPENING_PREREGISTRATION.md` before spending another crypto slot.
- **Exit evidence.** Reproducible manifest and coverage report, explicit pass or hold decision, and a hash-verifiable research record for any preregistered run. Treat the ~1% daily target as an unverified hypothesis.

## Phase 3 — Prove the production operator path in paper mode

**How this serves the primary goal:** Operators rehearse the tested controls and recovery procedures on the deployment they will use.

- Pick one supported production profile first. I recommend Compose with Caddy and PostgreSQL because that matches the checked-in production runbook. Pin version and image across supported profiles. Verify production-mode startup, auth and CORS requirements, Telegram permissions, dashboard and research assets, REST/OpenAPI agreement, and guarded metrics ingress at the proxy. Confirm monitoring targets match the selected topology and page a test recipient.
- Rehearse paper fills, broker or feed outage, alerting, backup and restore, restart and order reconciliation. Validate diagnostics and readiness at the reverse proxy. Review n8n tuning against the parameter promotion rules and prove that a failed or unreviewed candidate cannot replace active parameters. Use `RUNBOOK.md:107–165`, `scripts/e2e_smoke.py`, `tests/test_dashboard_mount.py`, and `tests/test_telegram_auth.py`.
- **Exit evidence.** Authenticated production-profile paper smoke log, backup and restore record, delivered and acknowledged alert, image size record, and a versioned runbook for start, halt, recovery and rollback.

## Phase 4 — Independent verification and release decision

**How this serves the primary goal:** Research and deployment can advance only when the actual configuration and evidence satisfy the relevant gate.

- Re-run CI against the candidate revision including the PostgreSQL suite, Docker smoke and research ledger freshness. Repeat the phase exit checks with the candidate image and production-equivalent config. Update changelog and runbook alongside each functional change.
- Record distinct decisions for research eligibility, paper operability and live readiness. For any future live request, follow the explicit operator sign-off, small-scope rollout and rollback criteria in `RUNBOOK.md:107–165`. Equity execution would need its own broker and compliance design. The immediate deliverables are reconciled evidence, verified kill controls and reproducible paper operation.
- **Exit evidence.** A decision record linking each gate to its exact revision, environment, test result, responsible operator and any remaining blockers.
