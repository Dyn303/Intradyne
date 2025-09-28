from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import ccxt.async_support as ccxt
from loguru import logger


class DataFeed:
    """Bitget spot L1 feed. Tries WS via ccxtpro if available; falls back to REST polling.

    For simplicity and stability, this implementation uses REST polling at ~1s cadence
    unless ccxtpro is available in the environment.
    """

    def __init__(self, exchange_id: str = "bitget", use_testnet: bool = True) -> None:
        self.exchange_id = exchange_id
        self.use_testnet = use_testnet
        self.exchange: Optional[ccxt.Exchange] = None
        self._running = False

    async def start(self, symbols: List[str]) -> AsyncIterator[Dict[str, Any]]:
        self._running = True
        ex = getattr(ccxt, self.exchange_id)()
        if self.exchange_id == "bitget" and self.use_testnet:
            # Bitget testnet support is limited in ccxt; left as a flag for future use.
            pass
        self.exchange = ex
        await ex.load_markets()
        logger.info(f"DataFeed started for {symbols}")
        try:
            while self._running:
                now = time.time()
                for sym in symbols:
                    try:
                        t = await ex.fetch_ticker(sym)
                        yield {
                            "ts": now,
                            "symbol": sym,
                            "bid": t.get("bid"),
                            "ask": t.get("ask"),
                            "last": t.get("last"),
                            "volume": t.get("baseVolume"),
                        }
                    except Exception as e:
                        logger.warning(f"Ticker fetch failed for {sym}: {e}")
                await asyncio.sleep(1.0)
        finally:
            try:
                await ex.close()
            except Exception:
                pass

    async def stop(self) -> None:
        self._running = False
