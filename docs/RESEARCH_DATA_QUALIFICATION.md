# Phase 2 research data qualification

This decision follows the [Phase 0 baseline](SYSTEM_BASELINE_PHASE_0.md) and [system plan](SYSTEM_REVIEW_PLAN.md). Local implementation evidence is from `71f7fd8`. The weekly scorer and its historical cache live in the separate Intradyne cloud workspace. The local daily diagnostic uses different identities and dates and has a separate denominator.

## Acceptance and decision rules

- Use SPUS filing effective dates for membership and join security identities across verified corporate actions. Preserve the date interval, price source, cadence, and SHA-256 of each input.
- Require at least 80% of departed identities to have usable weekly observations in at least 80% of their eligible whole ISO weeks. Also require at least 80% name coverage in every completed filing window. Track unresolved labels and exclusions explicitly. Run the daily measurement separately.
- For Alpha Vantage, provider access requires a usable listed-symbol control and departed-symbol data covering the relevant interval. HTTP success alone is insufficient. A missing binding, service error, or short history leaves provider qualification unresolved.
- Release equity Slot 1 only after its data, execution-cost, liquidity, and statistical-power preconditions are met. Fix the controls, concentration limits, dates, and chronological holdout before trials. Apply the holdout verdict only after a permitted run. Preserve the [research programme stop rules](EQUITY_PROGRAMME_STOP_RULE.md) and [Slot 1 preregistration](SLOT_1_PREREGISTRATION.md).

## Equity decision — hold

The weekly scorer at cloud commit `e7c00bc4ea2c9e38fc5b559d0510c6120c53d2f1` returned **80 of 106 departed identities (75.5%)**, five short of the floor, and exited `2`. All 24 completed filing windows passed the separate per-window rule. The lowest covered 162 of 180 names (90.0%) from 2020-07-28 to 2020-10-28. The audit ends on 2026-07-29, exclusive. The Phase 0 local daily diagnostic was 90 of 120 CUSIPs (75.0%) and is kept separate.

The weekly denominator is the set of distinct normalized common-stock identities across the filing timeline, excluding those in the final filing. The scorer applies verified CUSIP and ISIN continuity and excludes the documented zero-valued ABIOMED contingent value rights. It reported zero unresolved filing labels. Among the 26 identifiable departures lacking qualifying coverage, 20 need delisted prices, five have unmapped historical identities, and one needs an alternate price feed. In the cache, ABMD has zero usable observations across 132 eligible weeks and 11 filing windows. It needs 106 usable weekly observations.

```bash
PYTHONPATH=src .venv/bin/python scripts/recover_delisted.py \
  --weekly-cache /workspace/scratch/intradyne-equity/alias-coverage.4QPKML
```

The draft declares `ALPHAVANTAGE_API_KEY` for the exact HTTPS destination `www.alphavantage.co`. The published instance available to the task lacked an active binding. The task had no fresh-instance control, so the authenticated IBM control and ABMD provider request were skipped. No suitable authorized replacement source was available. Record a hold and leave the slot unspent.

The **Set up Intradyne** cloud task exported `/workspace/shared/downloads/intradyne-phase2.zip`. It contains six verified evidence files, including raw gate output, gaps by departed identity and filing window, and a checksum manifest for all 287 weekly input files. It contains neither raw prices nor credentials. The archive stays with the cloud task.

## Verification

- The cloud weekly regression suite passed 59 tests with zero skips. The cloud worktree stayed clean.
- The local qualification suite passed 148 tests covering the provider response classifier, identity resolution, point-in-time panel, data determinism, strategy eligibility, and research ledger integrity.
- The full local suite passed 1,042 tests with PostgreSQL 16 enabled and no skips reported. Ruff lint and format, mypy, and the research hash-chain check passed. Starlette emitted one AnyIO deprecation warning.

## Crypto and research records

The [crypto programme](CRYPTO_REOPENING_PREREGISTRATION.md) remains closed after its preregistered holdout failed. This phase uses no research slot and promotes no parameters. Any future permitted sub-minute study needs native Tardis trade inputs with provenance and checksums. Reject minute-derived bars, interpolation, and synthetic fallback when qualifying evidence. Candidates must have a separate chronological holdout and an explicit promotion decision. The ~1% daily return target remains an unverified hypothesis.

The existing research ledger verified before qualification. Source validation and coverage checks do not create a strategy experiment entry.
