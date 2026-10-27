# 🧠 Project Memory

## 🎯 Current Focus
- Integrating Tardis.dev for high-resolution historical data (trades to OHLCV).
- Connecting downloaded data into the Optuna backtester.

## 📝 Next Steps
- [x] Debug and fix the GitHub CI failure (resolved: tolerance window in get_high, instance-scoped _system_halted, ruff format).
- [ ] Test the Tardis downloader script with a real download.
- [ ] Connect the downloaded data directly into the Optuna backtester.

## 📚 Recent Discoveries & Decisions
- **Guardrails (Phase 2 Fixes):** Replaced process-wide global `SystemHalted` with instance-scoped `self._system_halted` to prevent test-suite pollution. Updated `MarkStore.get_high()` to incorporate `tolerance_seconds` so boundary ticks (e.g. at 1h lookback) are not dropped due to microsecond execution jitter. Flash-crash now checks `max(p_1h_high, p_1h)` while requiring valid 1h historical marks.
- **Data Provider:** Decided on Tardis.dev for micro-scalping data because it provides the tick-level granularity (order book, trades) needed for 1s/5s timeframes, whereas others are too slow, rate-limited, or expensive.
- **Tardis Connector:** Created `scripts/download_tardis.py` to fetch Tardis tick data and automatically resample it into Intradyne's `1m` or `5s` OHLCV CSV format.

## 🏛️ Archived Decisions
- *None yet.*
