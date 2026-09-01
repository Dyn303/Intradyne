from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable

import pandas as pd

from intradyne.engine.data_loader import DataLoader, LoaderConfig


async def _load_symbol(
    sym: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    data_dir: Path,
    exchange: str,
    allow_network: bool = False,
) -> pd.DataFrame:
    dl = DataLoader(
        LoaderConfig(data_dir=data_dir, exchange=exchange, allow_network=allow_network)
    )
    df = await dl.load_ohlcv(sym, timeframe, start_ms, end_ms, use_cache=True)
    return df.rename(columns={"timestamp": "ts"})


def load_ohlcv_many(
    symbols: Iterable[str],
    timeframe: str,
    start_ms: int,
    end_ms: int,
    data_dir: Path,
    exchange: str,
    allow_network: bool = False,
) -> dict[str, pd.DataFrame]:
    """Load bars for training.

    ``allow_network`` is opt-in: assembling a training set from whatever the
    exchange happens to return today is a deliberate act, not a fallback.
    """

    async def _runner() -> dict[str, pd.DataFrame]:
        tasks = [
            _load_symbol(
                s, timeframe, start_ms, end_ms, data_dir, exchange, allow_network
            )
            for s in symbols
        ]
        dfs = await asyncio.gather(*tasks)
        return {s: d for s, d in zip(symbols, dfs)}

    return asyncio.run(_runner())
