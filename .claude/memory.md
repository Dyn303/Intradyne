# 🧠 Project Memory

## 🎯 Current Focus
- Running multi-day backtest sweeps and strategy calibration on tick-resampled datasets.
- Testnet soak and alert validation ahead of Phase 6 live readiness.

## 📝 Next Steps
- [x] Debug and fix the GitHub CI failure (resolved: tolerance window in get_high, instance-scoped _system_halted, ruff format).
- [x] Test the Tardis downloader script with a real download (resolved: updated to tardis_dev v5 API with download_datasets_async, downloaded BTC/USDT and ETH/USDT 1m bars for Bitget).
- [x] Connect the downloaded data directly into the Optuna backtester (verified: backtest and optimize both execute cleanly against data/bitget/*.csv).
- [x] Run API E2E smoke test suite (verified: all 10 checks passed on local Uvicorn instance via scripts/e2e_smoke.py).
- [ ] Run multi-day or sub-minute (5s) optimization sweep to tune production params.

## 📚 Recent Discoveries & Decisions
- **Guardrails (Phase 2 Fixes):** Replaced process-wide global `SystemHalted` with instance-scoped `self._system_halted` to prevent test-suite pollution. Updated `MarkStore.get_high()` to incorporate `tolerance_seconds` so boundary ticks (e.g. at 1h lookback) are not dropped due to microsecond execution jitter. Flash-crash now checks `max(p_1h_high, p_1h)` while requiring valid 1h historical marks.
- **Data Provider:** Decided on Tardis.dev for micro-scalping data because it provides the tick-level granularity (order book, trades) needed for 1s/5s timeframes, whereas others are too slow, rate-limited, or expensive.
- **Tardis Connector (v5 API):** Upgraded `scripts/download_tardis.py` to use `download_datasets_async` and `download_dir` from `tardis_dev` 5.x. Resampling accurately aggregates trade ticks into standard OHLCV format (`open`, `high`, `low`, `close`, `volume`) with millisecond timestamps, perfectly matching `DataLoader` expectations.

## 🏛️ Archived Decisions
- **2026-11-25 Phase 1:** Implemented durable operator halt, consistent operator release authorization, queued-order reconciliation and exclusive audit writer ownership on `codex/phase-1-safety`. The full PostgreSQL-enabled run passed 1,041 tests. Separate exact-threshold and unavailable-store checks passed. Docker built successfully, passed 10 smoke checks and preserved the halt and audit ledger across a paper restore. Live trading remains gated. Phase 2 starts with weekly data coverage and provider access.
- **2026-11-25 Phase 0 baseline:** `docs/SYSTEM_BASELINE_PHASE_0.md` records exact local, GitHub and cloud revisions. Cloud `e7c00bc4ea2c9e38fc5b559d0510c6120c53d2f1` is 18 commits ahead and reproduces weekly departed coverage of 80/106. Its evidence archive includes weekly and durable-halt patches. Local `18f9d5e` passed 1,028 tests with PostgreSQL, Ruff, mypy, ledger freshness, Docker build and 10 smoke checks. The halt patch remains a Phase 1 candidate. Equity research remains under its coverage gate.
- **2026-11-25 system review:** See `docs/SYSTEM_REVIEW_PLAN.md` for the repository-wide findings and phased plan. Local `18f9d5e` lacks cloud commit `e7c00bc` and uses a daily coverage calculation. Reconcile revisions and evidence before using the reported weekly 80/106 result. Prioritize restart-safe halt, consistent release authorization, provider access and paper-mode production verification. Local test tooling and Docker were unavailable during this review.
- *None yet.*
