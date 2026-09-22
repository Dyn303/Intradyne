# 🧠 Project Memory

## 🎯 Current Focus
- Integrating Tardis.dev for high-resolution historical data (trades to OHLCV).
- Fixing CI/CD build and test failures after the latest commits.

## 📝 Next Steps
- [ ] Debug and fix the GitHub CI failure.
- [ ] Test the Tardis downloader script with a real download.
- [ ] Connect the downloaded data directly into the Optuna backtester.

## 📚 Recent Discoveries & Decisions
- **Data Provider:** Decided on Tardis.dev for micro-scalping data because it provides the tick-level granularity (order book, trades) needed for 1s/5s timeframes, whereas others are too slow, rate-limited, or expensive.
- **Tardis Connector:** Created `scripts/download_tardis.py` to fetch Tardis tick data and automatically resample it into Intradyne's `1m` or `5s` OHLCV CSV format.

## 🏛️ Archived Decisions
- *None yet.*
