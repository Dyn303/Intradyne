"""The spread a backtest prices its fills against.

OHLCV carries no spread, so one has to be assumed, and the assumption decides
what the backtest concludes. `bars_to_l1` hardcoded 1.0 and nothing ever
passed anything else, so every instrument was modelled at one basis point --
near enough on the liquid names, 10bps optimistic on DOT, which is exactly
where an edge lives or dies.
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from intradyne.core.config import Settings
from intradyne.engine.data_loader import DataLoader


def _bars(close=100.0, n=3):
    return pd.DataFrame(
        {
            "timestamp": [1_000 * i for i in range(n)],
            "open": [close] * n,
            "high": [close] * n,
            "low": [close] * n,
            "close": [close] * n,
            "volume": [1.0] * n,
        }
    )


def _spread_of(row):
    return (row["ask"] - row["bid"]) / row["last"] * 10_000.0


@pytest.mark.parametrize("bps", [0.0, 1.0, 4.0, 11.38])
def test_the_quote_carries_the_spread_it_was_given(bps):
    row = next(iter(DataLoader.bars_to_l1(_bars(), bps)))
    assert _spread_of(row) == pytest.approx(bps, abs=1e-9)


def test_the_default_is_the_widest_spread_the_live_filter_admits():
    """Coherent with the running system: it refuses anything wider, so the
    bound is the conservative reading of what a fill could have cost."""
    s = Settings()
    assert s.backtest_spread_bps == float(s.max_spread_bps)


def test_a_bar_still_carries_its_ohlc():
    row = next(iter(DataLoader.bars_to_l1(_bars(close=250.0), 4.0)))
    assert row["last"] == 250.0
    assert row["open"] == row["high"] == row["low"] == 250.0
    assert row["bid"] < row["last"] < row["ask"]


def test_an_empty_frame_yields_nothing():
    assert list(DataLoader.bars_to_l1(pd.DataFrame(), 4.0)) == []


class TestPerSymbolOverride:
    """A single figure cannot be right for BTC at 0.00bps and DOT at 11.38."""

    def test_each_symbol_gets_its_own_measured_spread(self, monkeypatch, tmp_path):
        from intradyne.engine.data_loader import LoaderConfig

        loader = DataLoader(LoaderConfig(data_dir=tmp_path, exchange="bitget"))

        async def fake_load(sym, tf, a, b):
            return _bars(close=100.0, n=2)

        monkeypatch.setattr(loader, "load_ohlcv", fake_load)

        async def collect():
            seen = {}
            async for sym, bar in loader.multi_symbol_stream(
                ["BTC/USDT", "DOT/USDT"],
                "1m",
                0,
                10_000,
                spread_bps=4.0,
                spread_bps_by_symbol={"DOT/USDT": 11.38},
            ):
                seen.setdefault(sym, _spread_of(bar))
            return seen

        seen = asyncio.run(collect())
        assert seen["BTC/USDT"] == pytest.approx(4.0, abs=1e-9)
        assert seen["DOT/USDT"] == pytest.approx(11.38, abs=1e-9)
