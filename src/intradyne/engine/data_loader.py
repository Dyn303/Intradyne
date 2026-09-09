from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
)

import pandas as pd
import ccxt.async_support as ccxt

# Timeframe helpers
TF_MAP_SEC: Dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 4 * 3600,
    "1d": 24 * 3600,
}


def timeframe_to_seconds(tf: str) -> int:
    if tf not in TF_MAP_SEC:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return TF_MAP_SEC[tf]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class DataUnavailable(RuntimeError):
    """No cached bars for a symbol, and fetching was not permitted.

    Raised instead of quietly falling back to the live exchange or to a
    fabricated random walk. A backtest that cannot find its data must say so:
    silently measuring something else is worse than not measuring at all.
    """


@dataclass
class LoaderConfig:
    data_dir: Path
    exchange: str = "bitget"
    #: Allow a cache miss to fetch from the live exchange. Off by default:
    #: a backtest must be reproducible, and a run that reaches the network
    #: gets different bars every time it is repeated.
    allow_network: bool = False
    #: Allow a cache miss to fall back to a fabricated random walk. Off by
    #: default: synthetic bars are not a measurement, and reporting them as
    #: one is how a backtest comes to state a PnL for data it never had.
    allow_synthetic: bool = False


class DataLoader:
    def __init__(self, cfg: LoaderConfig) -> None:
        self.cfg = cfg
        _ensure_dir(self.cfg.data_dir)

    def _symbol_path(self, symbol: str, timeframe: str) -> Path:
        sym = symbol.replace("/", "-")
        root = self.cfg.data_dir / self.cfg.exchange
        _ensure_dir(root)
        return root / f"{sym}_{timeframe}.csv"

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        """Read cached bars without moving any price.

        read_csv's default parser is fast but not bit-exact: it shifts a
        good third of full-precision float64 values by one ULP. Every read of
        the same file must return the same numbers, or two runs of one
        backtest disagree.
        """
        return pd.read_csv(path, float_precision="round_trip")

    async def fetch_ohlcv_ccxt(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> pd.DataFrame:
        ex = getattr(ccxt, self.cfg.exchange)({"enableRateLimit": True})
        await ex.load_markets()
        tf = timeframe
        limit = 1000
        all_rows: List[List[Any]] = []
        since = start_ms
        while since < end_ms:
            batch = await ex.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=limit)
            if not batch:
                break
            all_rows.extend(batch)
            since = batch[-1][0] + timeframe_to_seconds(tf) * 1000
            await asyncio.sleep(ex.rateLimit / 1000.0)
            if batch[-1][0] >= end_ms:
                break
        await ex.close()
        if not all_rows:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )  # empty
        df = pd.DataFrame(
            all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)]
        return df

    async def load_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        path = self._symbol_path(symbol, timeframe)
        if use_cache and path.exists():
            df = self._read_csv(path)
        else:
            # If sub-minute timeframe, try synthesize from 1m cache
            if timeframe.endswith("s"):
                base_path = self._symbol_path(symbol, "1m")
                if base_path.exists():
                    df1m = self._read_csv(base_path)
                    df = self._synthesize_subminute(df1m, timeframe)
                else:
                    # Fallback: synthesize sub-minute directly
                    df = self._synthesize_fallback(
                        symbol, timeframe, start_ms, end_ms, path
                    )
            elif self.cfg.allow_network:
                try:
                    df = await self.fetch_ohlcv_ccxt(
                        symbol, timeframe, start_ms, end_ms
                    )
                except Exception as exc:
                    raise DataUnavailable(
                        f"fetching {symbol} {timeframe} from "
                        f"{self.cfg.exchange} failed: {exc}"
                    ) from exc
            else:
                df = self._synthesize_fallback(
                    symbol, timeframe, start_ms, end_ms, path
                )
            if not df.empty:
                df.to_csv(path, index=False)
                # Read back what was just written, so the run that primes the
                # cache sees exactly what every later run sees. Returning the
                # in-memory frame here made the first run of a backtest
                # disagree with the second on gross_pnl, max_dd and
                # profit_factor -- same seed, same process.
                df = self._read_csv(path)
        # Normalize
        if not df.empty:
            df = df.sort_values("timestamp").drop_duplicates("timestamp")
            # Filter to requested window, even when from cache
            df = df[(df["timestamp"] >= start_ms) & (df["timestamp"] <= end_ms)]
        return df

    @staticmethod
    def _synthesize_subminute(df1m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if df1m.empty:
            return df1m
        sec = timeframe_to_seconds(timeframe)
        if sec <= 0:
            return df1m
        # segments per 1m
        segments = max(1, 60 // sec)
        rows = []
        for _, r in df1m.iterrows():
            t0 = int(r["timestamp"])
            o0 = float(r["open"])
            c0 = float(r["close"])
            h0 = float(r["high"])
            l0 = float(r["low"])
            v0 = float(r.get("volume", 0.0))
            for k in range(segments):
                ts = t0 + k * sec * 1000
                # Linear interpolation for open/close within minute
                f1 = k / segments
                f2 = (k + 1) / segments
                o = o0 + (c0 - o0) * f1
                c = o0 + (c0 - o0) * f2
                hi = max(h0, o, c)
                lo = min(l0, o, c)
                vol = v0 / segments
                rows.append([ts, o, hi, lo, c, vol])
        return pd.DataFrame(
            rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    def _synthesize_fallback(
        self, symbol: str, timeframe: str, start_ms: int, end_ms: int, path: Path
    ) -> pd.DataFrame:
        """Fabricate bars, but only where that was explicitly asked for."""
        if not self.cfg.allow_synthetic:
            raise DataUnavailable(
                f"no cached bars for {symbol} {timeframe}: expected {path}. "
                "Set allow_network=True to fetch them, or allow_synthetic=True "
                "to run against a fabricated series -- results from synthetic "
                "bars are not a measurement of anything."
            )
        return self._synthesize_direct(symbol, timeframe, start_ms, end_ms)

    @staticmethod
    def _stable_seed(symbol: str) -> int:
        """A per-symbol seed that is the same in every process.

        This used ``abs(hash(symbol))``. Python salts str hashing per
        interpreter (PYTHONHASHSEED), so the "deterministic" synthetic series
        was a different random walk on every run, and the backtest's ``seed``
        argument had no bearing on it whatsoever.
        """
        return zlib.crc32(symbol.encode("utf-8"))

    @staticmethod
    def _synthesize_direct(
        symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> pd.DataFrame:
        # Deterministic synthetic OHLCV via seeded random walk
        import numpy as np

        sec = timeframe_to_seconds(timeframe)
        if sec <= 0:
            sec = 60
        n = max(1, int((end_ms - start_ms) // (sec * 1000)))
        _seed = DataLoader._stable_seed(symbol)
        rs = np.random.RandomState(_seed % (2**32))
        base = 100.0 + (_seed % 100) * 0.1
        rets = rs.normal(loc=0.0, scale=0.0005, size=n)
        prices = [base]
        for r in rets:
            prices.append(prices[-1] * (1.0 + r))
        rows = []
        for i in range(n):
            ts = start_ms + i * sec * 1000
            o = float(prices[i])
            c = float(prices[i + 1])
            hi = max(o, c) * (1.0 + 0.0008)
            lo = min(o, c) * (1.0 - 0.0008)
            vol = 5.0 + (i % 7)
            rows.append([ts, o, hi, lo, c, vol])
        return pd.DataFrame(
            rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    @staticmethod
    def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if df.empty:
            return df
        s = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index(s)
        rule = timeframe
        ohlc = (
            df[["open", "high", "low", "close", "volume"]]
            .resample(rule)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        ohlc["timestamp"] = ohlc.index.view("int64") // 1_000_000
        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        return ohlc[cols]

    @staticmethod
    def bars_to_l1(
        df: pd.DataFrame, spread_bps: float = 1.0
    ) -> Iterator[Dict[str, Any]]:
        """Synthesise an L1 quote from an OHLCV bar.

        OHLCV carries no spread, so one has to be assumed, and the assumption
        decides what a backtest concludes. `spread_bps` defaulted to 1.0 and
        nothing ever passed anything else, so every instrument was modelled at
        one basis point. Measured on Bitget against a round trip that also
        pays 4bps slippage and 10bps taker fees:

            symbol      real    at 1bps    understated by
            BTC/USDT   14.00      15.00            -1.00
            LTC/USDT   15.96      15.00            +0.96
            ADA/USDT   18.51      15.00            +3.51
            DOT/USDT   25.38      15.00           +10.38

        Roughly neutral on the liquid names and badly optimistic on the thin
        ones -- which are exactly the names where an edge lives or dies. The
        caller now supplies this; see `multi_symbol_stream`.
        """
        if df.empty:
            # A generator returns by stopping; `return iter(())` here was a
            # value-return inside a generator, which Python ignores.
            return
        for _, row in df.iterrows():
            ts = int(row["timestamp"]) / 1000.0
            mid = float(row["close"])
            spr = mid * (spread_bps / 10_000.0)
            yield {
                "ts": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "last": mid,
                "bid": mid - spr / 2,
                "ask": mid + spr / 2,
                "volume": float(row.get("volume", 0.0)),
            }

    async def multi_symbol_stream(
        self,
        symbols: List[str],
        timeframe: str,
        start_ms: int,
        end_ms: int,
        spread_bps: float = 1.0,
        spread_bps_by_symbol: Optional[Mapping[str, float]] = None,
    ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
        """Merge per-symbol bars into one time-ordered stream.

        `spread_bps` is the cost assumption every fill in a backtest is priced
        against, and `spread_bps_by_symbol` overrides it per instrument where
        a real measurement exists -- a single figure cannot be right for both
        BTC at 0.00bps and DOT at 11.38.
        """
        # Load all symbols then merge by timestamp using heap
        import heapq

        frames: Dict[str, pd.DataFrame] = {}
        for s in symbols:
            df = await self.load_ohlcv(s, timeframe, start_ms, end_ms)
            frames[s] = df

        by_symbol = spread_bps_by_symbol or {}
        iters: Dict[str, Iterator[Dict[str, Any]]] = {
            s: self.bars_to_l1(
                frames[s], float(by_symbol.get(s, spread_bps))
            ).__iter__()
            for s in symbols
        }
        heap: List[Tuple[float, str, Dict[str, Any]]] = []
        for s, it in iters.items():
            try:
                v = next(it)
                heap.append((v["ts"], s, v))
            except StopIteration:
                pass
        heapq.heapify(heap)
        while heap:
            ts, s, v = heapq.heappop(heap)
            yield s, v
            it = iters[s]
            try:
                nxt = next(it)
                heapq.heappush(heap, (nxt["ts"], s, nxt))
            except StopIteration:
                pass
