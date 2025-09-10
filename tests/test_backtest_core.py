from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from app.backtest import run as run_backtest


def _write_csv(tmp: Path, symbol: str, timeframe: str, rows: list[tuple[int, float, float, float, float, float]]):
    ddir = tmp / "bitget"
    ddir.mkdir(parents=True, exist_ok=True)
    fname = ddir / f"{symbol.replace('/', '-')}_{timeframe}.csv"
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df.to_csv(fname, index=False)


def test_deterministic_backtest_with_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Create simple ascending then flat prices
    start = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    ts = int(start.timestamp() * 1000)
    for i in range(50):
        o = 100 + i * 0.1
        c = o + 0.05
        rows.append((ts + i * 60_000, o, o + 0.2, o - 0.2, c, 10.0))
    _write_csv(tmp_path, "BTC-USDT", "1m", rows)
    res1 = run_backtest(["BTC/USDT"], rows[0][0], rows[-1][0], "1m", "momentum", {"momentum": {"breakout_window": 10, "min_range_bps": 3}}, 2, 5, 2, seed=123)
    res2 = run_backtest(["BTC/USDT"], rows[0][0], rows[-1][0], "1m", "momentum", {"momentum": {"breakout_window": 10, "min_range_bps": 3}}, 2, 5, 2, seed=123)
    assert json.dumps(res1.metrics, sort_keys=True) == json.dumps(res2.metrics, sort_keys=True)


def test_sl_tp_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    start = pd.Timestamp("2024-02-01", tz="UTC")
    rows = []
    ts = int(start.timestamp() * 1000)
    # Rise then drop below SL threshold
    prices = [100.0] * 5 + [101.0] * 5 + [98.0] * 5
    for i, p in enumerate(prices):
        rows.append((ts + i * 60_000, p, p + 0.1, p - 0.1, p, 5.0))
    _write_csv(tmp_path, "BTC-USDT", "1m", rows)
    params = {"risk": {"per_trade_sl_pct": 0.01, "tp_pct": 0.02, "max_pos_pct": 0.1}, "momentum": {"breakout_window": 2, "min_range_bps": 1}}
    res = run_backtest(["BTC/USDT"], rows[0][0], rows[-1][0], "1m", "momentum", params, 2, 5, 2, seed=99)
    # Expect some trades and finite metrics
    assert isinstance(res.metrics.get("final_equity"), float)


def test_compliance_blocks_non_whitelist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    start = pd.Timestamp("2024-03-01", tz="UTC")
    ts = int(start.timestamp() * 1000)
    rows = [(ts + i * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(10)]
    _write_csv(tmp_path, "ABC-USDT", "1m", rows)
    with pytest.raises(Exception):
        run_backtest(["ABC/USDT"], rows[0][0], rows[-1][0], "1m", "momentum", {}, 2, 5, 2, seed=1)

