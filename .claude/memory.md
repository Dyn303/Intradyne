# 🧠 Project Memory

## 🎯 Current Focus
- Running multi-day backtest sweeps and strategy calibration on tick-resampled datasets.
- Testnet soak and alert validation ahead of Phase 6 live readiness.

## 📝 Next Steps
- [x] Debug and fix the GitHub CI failure (resolved: tolerance window in get_high, instance-scoped _system_halted, ruff format).
- [x] Test the Tardis downloader script with a real download (resolved: updated to tardis_dev v5 API with download_datasets_async, downloaded BTC/USDT and ETH/USDT 1m bars for Bitget).
- [x] Connect the downloaded data directly into the Optuna backtester (verified: backtest and optimize both execute cleanly against data/bitget/*.csv).
- [ ] Run multi-day or sub-minute (5s) optimization sweep to tune production params.

## 📚 Recent Discoveries & Decisions
- **Guardrails (Phase 2 Fixes):** Replaced process-wide global `SystemHalted` with instance-scoped `self._system_halted` to prevent test-suite pollution. Updated `MarkStore.get_high()` to incorporate `tolerance_seconds` so boundary ticks (e.g. at 1h lookback) are not dropped due to microsecond execution jitter. Flash-crash now checks `max(p_1h_high, p_1h)` while requiring valid 1h historical marks.
- **Data Provider:** Decided on Tardis.dev for micro-scalping data because it provides the tick-level granularity (order book, trades) needed for 1s/5s timeframes, whereas others are too slow, rate-limited, or expensive.
- **Tardis Connector (v5 API):** Upgraded `scripts/download_tardis.py` to use `download_datasets_async` and `download_dir` from `tardis_dev` 5.x. Resampling accurately aggregates trade ticks into standard OHLCV format (`open`, `high`, `low`, `close`, `volume`) with millisecond timestamps, perfectly matching `DataLoader` expectations.

## 🏛️ Archived Decisions
- *None yet.*
