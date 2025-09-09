from __future__ import annotations

import argparse
from src.backtester.engine import run_backtest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--symbols", type=str, default="BTC,ETH")
    args = p.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    # normalize to BASE/USDT
    syms = [s if "/" in s else f"{s}/USDT" for s in symbols]
    n = run_backtest(days=args.days, symbols=syms)
    print(f"backtest_completed orders={n}")


if __name__ == "__main__":
    main()

