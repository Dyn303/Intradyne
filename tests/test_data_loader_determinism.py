"""The loader must return identical bars every time, and never invent them.

These cover the three ways a backtest could silently stop measuring what it
claims to measure: the cache priming itself with different bytes than it
hands back, a cache miss reaching the live exchange, and the synthetic
fallback being seeded from a value that changes every process.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from intradyne.engine.data_loader import (
    DataLoader,
    DataUnavailable,
    LoaderConfig,
)


def _rows(n: int = 50) -> list[tuple[int, float, float, float, float, float]]:
    ts = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)
    out = []
    for i in range(n):
        o = 100 + i * 0.1
        out.append((ts + i * 60_000, o, o + 0.2, o - 0.2, o + 0.05, 10.0))
    return out


def _write_csv(root: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    ddir = root / exchange
    ddir.mkdir(parents=True, exist_ok=True)
    fp = ddir / f"{symbol.replace('/', '-')}_{timeframe}.csv"
    pd.DataFrame(
        _rows(), columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).to_csv(fp, index=False)
    return fp


def test_cache_miss_then_hit_returns_identical_bars(tmp_path: Path):
    """The run that primes the cache must see exactly what later runs see.

    load_ohlcv used to write the cache and then return the *in-memory* frame,
    so the priming run and every subsequent run read different bytes. The CSV
    round trip is not bit-exact under read_csv's default parser, so a
    backtest silently produced different PnL on its first run than on its
    second -- from the same seed, in the same process.
    """
    cfg = LoaderConfig(data_dir=tmp_path, exchange="bitget", allow_synthetic=True)
    dl = DataLoader(cfg)
    rows = _rows()
    start, end = rows[0][0], rows[-1][0]

    first = asyncio.run(dl.load_ohlcv("BTC/USDT", "1m", start, end))
    assert not first.empty
    second = asyncio.run(dl.load_ohlcv("BTC/USDT", "1m", start, end))

    pd.testing.assert_frame_equal(first, second, check_exact=True)


def test_csv_round_trip_is_bit_exact(tmp_path: Path):
    """Reading a cached file back must not move any price by an ULP."""
    fp = _write_csv(tmp_path, "bitget", "BTC/USDT", "1m")
    # Values chosen to need the full 17 significant digits.
    df = pd.DataFrame({"close": [100.31402699468643, 103.45493636459607]})
    df.to_csv(fp, index=False)

    dl = DataLoader(LoaderConfig(data_dir=tmp_path, exchange="bitget"))
    back = dl._read_csv(fp)
    assert list(back["close"].values) == list(df["close"].values)


def test_cache_miss_does_not_reach_the_network(tmp_path: Path):
    """A miss must fail loudly, not quietly substitute live market data."""
    dl = DataLoader(LoaderConfig(data_dir=tmp_path, exchange="bitget"))

    async def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("the loader reached for the live exchange")

    dl.fetch_ohlcv_ccxt = _explode  # type: ignore[method-assign]

    with pytest.raises(DataUnavailable) as ei:
        asyncio.run(dl.load_ohlcv("BTC/USDT", "1m", 0, 1))
    # The error has to say which file it wanted, or it is not actionable.
    assert "BTC-USDT_1m.csv" in str(ei.value)


def test_network_fetch_requires_explicit_opt_in(tmp_path: Path):
    """allow_network=True is the only way to contact the exchange."""
    dl = DataLoader(
        LoaderConfig(data_dir=tmp_path, exchange="bitget", allow_network=True)
    )
    called: list[str] = []

    async def _fake(symbol, timeframe, start_ms, end_ms):
        called.append(symbol)
        return pd.DataFrame(
            _rows(), columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    dl.fetch_ohlcv_ccxt = _fake  # type: ignore[method-assign]
    rows = _rows()
    df = asyncio.run(dl.load_ohlcv("BTC/USDT", "1m", rows[0][0], rows[-1][0]))
    assert called == ["BTC/USDT"]
    assert not df.empty


def test_synthetic_series_is_identical_across_processes(tmp_path: Path):
    """The synthetic fallback must not be seeded from hash().

    hash(str) is salted per process (PYTHONHASHSEED), so the "deterministic
    synthetic OHLCV" was a different random walk in every interpreter, and
    the run's seed= argument did not control it at all.
    """
    prog = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, r"{src}")
        from intradyne.engine.data_loader import DataLoader
        df = DataLoader._synthesize_direct("BTC/USDT", "1m", 0, 600_000)
        print(repr(list(df["close"])[:5]))
        """
    ).format(src=str(Path(__file__).resolve().parents[1] / "src"))

    outs = {
        subprocess.run(
            [sys.executable, "-c", prog], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(outs) == 1, f"synthetic data differs between processes: {outs}"
