# Intradyne Phase 0 baseline

Captured on 2026-11-25. This is the evidence companion to `SYSTEM_REVIEW_PLAN.md`.

## Revision and environment matrix

| Environment | Revision | Evidence |
| --- | --- | --- |
| Windows checkout | `18f9d5e998cab3ad67ef5f81e6cce95570c72e5e` | `git rev-parse HEAD`; the worktree contains documentation changes. Phase 0 tooling uses an isolated CPython 3.11.16 environment under `.venv/phase0`. |
| GitHub `HEAD` and `master` | `18f9d5e998cab3ad67ef5f81e6cce95570c72e5e` | Direct `git ls-remote`; [CI run 40466252749](https://github.com/Dyn303/Intradyne/actions/runs/40466252749) passed at that SHA. [Dependabot run 41157565480](https://github.com/Dyn303/Intradyne/actions/runs/41157565480) separately failed an SDK dependency update. |
| Intradyne cloud task, clean `work` | `e7c00bc4ea2c9e38fc5b559d0510c6120c53d2f1` | Cloud Git reports 18 commits ahead and zero behind, with GitHub master as the merge base. Its immediate parent is `a31c79be0581a6d529a72c589ac17ca85791d0d7`. GitHub's commit API did not have the cloud commit abbreviation and the local object store does not have it. |

## Coverage and halt evidence

- **Cloud weekly, reproduced at cloud HEAD.** `PYTHONPATH=src .venv/bin/python scripts/recover_delisted.py --weekly-cache /workspace/scratch/intradyne-equity/alias-coverage.4QPKML` exited 2. All 24 completed filing windows passed. The departed gate failed at 80/106 qualifying identities (75.5%), five short. The denominator is normalized identities across filings minus identities in the final filing. Verified corporate-action continuity and exclusion of identified ABIOMED contingent value rights apply. The audit covers 2020-07-28 inclusive through 2026-07-29 exclusive and uses whole ISO weeks within filing windows. The cloud cache manifest has 287 weekly files with individual hashes and source basis. This was a cached run. The separate Alpha Vantage request produced no prices and the active HTTP secret binding was absent during that probe.
- **Local daily, reproduced from cache.** 90/120 departed CUSIPs (75.0%) met the per-name weekday floor using `holdings`, `cascade`, and `price_tail` in `scripts/recover_delisted.py`, with both provider fetch methods disabled and zero requests. The resolver identified 21 departed names as delisted. This is a cache-only diagnostic. The CLI withholds the provider-qualified P3 verdict when delisted histories were never requested (`:544–585`).
- **Local cache inventory.** `.venv/phase0/equity-cache-manifest.csv` records the path, cadence, first and last dated row, and SHA-256 of each of 308 files. Manifest SHA-256 is `a7d636b58ceaa0f02cd8193ac119fce89bcfab679d3fe48f6caaaab3622c9697`. It contains 286 daily files, six monthly files and 16 intraday files, with zero weekly files. The latest daily and monthly rows are dated 2026-09-08. Existing files lack verified per-file provider provenance. Cadence was inferred from cache paths and filenames.
- **Halt source.** Local `risk/kill_switch.py:23–42` stores operator halt in a process-global variable. `risk/guardrails.py:214,245–248` uses an instance-local automatic halt latch. The cloud implementation is in `dbc3d23a999b8968bc48d10b912f0637d2960445`, `d808b2ec01de7deb026e7b67b1abd59537babc51` and `920444a0081ee3dc24230c43f6a309ab3ebd6269`. Cloud test inventory covers PostgreSQL reopening, a fresh process, storage fail-closed behavior, both operator authorization routes, and unresolved submissions. The cloud task applied neither patch to master. Both the focused weekly patch and standalone halt patch passed `git apply --check --cached` in a temporary index rooted at master. Keep safety implementation and rollout in Phase 1.
- **Cloud evidence archive.** The cloud task exported `/workspace/shared/downloads/intradyne-phase0.zip` with the feasibility report, weekly and halt patches, all 18 commits, checksum manifest, exact coverage output and reproduction notes. It validated the nine-entry ZIP, all 287 checksum entries and the 18-commit count. The archive stays with the cloud task and contains no raw prices.
- **Reuse contracts.** `OrderKeyStore.reserve`, `complete`, `fail`, and `in_flight` live in `src/intradyne/core/idempotency.py:92–146`. See `src/intradyne/engine/reconcile.py:46` for startup reconciliation, `src/intradyne/research/spus_panel.py:60,180` for the price source protocol and panel entry point, `scripts/research_ledger.py:98–162` for provenance and verification, and `scripts/e2e_smoke.py:62–72` for the API surface checks.

## Verification

The exact-SHA GitHub CI passed Ruff lint and format, mypy (91 files), pytest with PostgreSQL 16 enabled, research log freshness, Docker build and all 10 container smoke checks. Its build log recorded image ID `sha256:692b25feae070c5ae633542d1d936e0bf212f98823fcdb2f8a80bef3d8b4a61d`. Local Ruff lint and format also passed (212 files formatted), and the local research log matched a clean regeneration from its intact three-record ledger.

Local mypy passed all 91 source files. The local full suite completed all 1,028 collected tests with `CI=true` and the temporary PostgreSQL 16 service configured. The suite exited 0 with no skips reported. Starlette emitted an AnyIO deprecation warning.

The fresh local Docker build passed. Image manifest digest `sha256:c076a713714dd25df724455d946239f6636209ef253a9bbaab1a5e56634a3772`. Docker reported 174,191,930 bytes for the image. The engine-disabled development container with SQLite passed all 10 smoke checks, including readiness, dashboard, risk status and the research record.

The local daily and cloud weekly baselines are separate observations. The cloud weekly gate remains a research dependency even after baseline reconciliation.
