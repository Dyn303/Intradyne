from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from loguru import logger


#: Target seconds between successive ticks for a symbol. The strategies size
#: their windows in *ticks* -- MomentumStrategy holds `deque(maxlen=120)`
#: commented "2m at 1s" -- so the interval is what converts a window length
#: into a span of time. One second is the figure they were written against.
TARGET_INTERVAL_S = 1.0

#: How far over the target a pass may run before it is worth warning about.
#: Kraken's batched call measures 1.0-1.2s against a 1.0s target, so a strict
#: comparison fires on ordinary jitter at every launch -- and a warning that
#: cries wolf on every launch is one nobody reads. The failure this exists to
#: catch was 10x over, not 20%.
SLOW_PASS_TOLERANCE = 1.5


class DataFeed:
    """Spot L1 feed. Websockets where the venue supports them, else polling.

    ## The interval is the thing to get right

    The strategies define their windows in ticks and document them in seconds:
    `MomentumStrategy` keeps `deque(maxlen=120)` commented "2m at 1s" and a
    `breakout_window` of 60 that reads as a minute. Nothing enforces that. The
    tick *interval* is what converts the two, so a feed that delivers slowly
    does not merely lag -- it silently stretches every lookback in the system.

    This polled one symbol at a time and slept a second after the whole pass,
    which made the interval `N x latency + 1s`. Measured against Kraken at
    1.1s per call:

        8 symbols   ->  9.9s between ticks   (windows stretched ~10x)
        15 symbols  -> 17.8s
        50 symbols  -> 56.9s

    So symbol count silently changed strategy behaviour, which is the opposite
    of what a configuration knob should do. A "60 second" breakout window was
    covering ten minutes while the 120-second time stop stayed literal.

    Two changes. Tickers are fetched in **one batched call** where the venue
    supports it, so the cost no longer scales with symbol count. And the sleep
    is the **remainder** of the target interval rather than a flat second, so
    the cycle aims at a fixed period instead of drifting with latency.

    What cannot be fixed here is stated rather than hidden: a REST feed whose
    round trip exceeds the target cannot deliver it. `interval_s` reports what
    was actually achieved, and `start` warns once when the two diverge, because
    a stretched window that nobody notices is how a backtested strategy quietly
    becomes a different one.
    """

    def __init__(self, exchange_id: str = "bitget", use_testnet: bool = True) -> None:
        self.exchange_id = exchange_id
        self.use_testnet = use_testnet
        self.exchange: Optional[Any] = None
        self._running = False
        #: Measured seconds between passes. None until the first completes.
        self.interval_s: Optional[float] = None
        self._warned_slow = False
        #: How the last pass got its prices: "websocket" or "rest".
        self.transport: str = "rest"
        #: Latest quote per symbol, kept current by the websocket task.
        self._latest: Dict[str, Any] = {}

    async def _watch(self, ex: Any, symbols: List[str]) -> None:
        """Keep `_latest` current from the venue's ticker stream.

        Deliberately does not yield anything. Emission cadence belongs to
        `start`, because the strategies count windows in ticks and a socket
        pushing ~10 updates a second would redefine every one of them.

        A socket that drops is not fatal -- `start` falls back to the REST
        snapshot for as long as `_latest` is empty -- so failures here are
        logged and retried rather than raised into the trading loop.
        """
        backoff = 1.0
        while self._running:
            try:
                tickers = await ex.watch_tickers(symbols)
                if tickers:
                    self._latest.update(tickers)
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                # Stale quotes are worse than none: a reconnect that silently
                # replays the last snapshot would price orders off a market
                # that has moved. Drop what we have and let the REST path
                # cover the gap.
                self._latest.clear()
                logger.warning(
                    f"ticker socket dropped ({e}); retrying in {backoff:.0f}s, "
                    "REST snapshot covering the gap"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def start(self, symbols: List[str]) -> AsyncIterator[Dict[str, Any]]:
        self._running = True
        if self.exchange is None:
            # Imported lazily: ccxt is only needed once the feed actually
            # starts, so the paper-only API image does not require it at
            # import time. A caller may set `exchange` first to supply its
            # own -- which is how the cadence above is tested without a
            # network, since latency is the whole subject.
            # ccxt Pro has shipped inside the free `ccxt` package since 4.0;
            # the separate commercial `ccxtpro` distribution this code used to
            # tell people to install is not how you get websockets any more.
            # Fall back to the REST client if the pro module is unavailable.
            try:
                import ccxt.pro as ccxt_pro

                self.exchange = getattr(ccxt_pro, self.exchange_id)()
            except Exception:  # noqa: BLE001
                import ccxt.async_support as ccxt

                self.exchange = getattr(ccxt, self.exchange_id)()
            if self.exchange_id == "bitget" and self.use_testnet:
                # Bitget testnet support is limited in ccxt; left as a flag
                # for future use.
                pass
        ex = self.exchange
        await ex.load_markets()
        logger.info(f"DataFeed started for {symbols}")
        batched = bool(getattr(ex, "has", {}).get("fetchTickers"))

        # Websockets feed the snapshot; they do not drive emission.
        #
        # Bitget pushes ~9.9 ticker updates a second. Yielding on each one
        # would take the tick rate from 0.36/s (a 2.8s REST pass) to 9.9/s,
        # and since the strategies count their windows in *ticks*, a 60-tick
        # window would collapse from 170 seconds to six. That is the same
        # defect as a slow feed, inverted and larger.
        #
        # So the socket keeps `_latest` current and the loop below emits on
        # the target interval regardless. Freshness comes from the socket;
        # cadence stays what the strategies were written against.
        watcher: Optional[asyncio.Task[None]] = None
        if bool(getattr(ex, "has", {}).get("watchTickers")):
            watcher = asyncio.create_task(self._watch(ex, symbols))
        try:
            while self._running:
                started = time.monotonic()
                now = time.time()

                tickers: Dict[str, Any] = {}
                if watcher is not None and self._latest:
                    # The socket has quotes; take a snapshot and skip the
                    # round trip entirely.
                    tickers = dict(self._latest)
                    self.transport = "websocket"
                elif batched:
                    self.transport = "rest"
                    # One call for every symbol. Against Kraken this is ~1.2s
                    # for eight, where eight sequential calls were ~8.9s -- and
                    # it no longer grows with the symbol count.
                    try:
                        tickers = await ex.fetch_tickers(symbols)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Batched ticker fetch failed: {e}")
                        batched = False
                if not batched:
                    for sym in symbols:
                        try:
                            tickers[sym] = await ex.fetch_ticker(sym)
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"Ticker fetch failed for {sym}: {e}")

                # Published before the yields: a consumer that reads one
                # tick and stops still learns what the tick was worth.
                fetch_s = time.monotonic() - started
                self.interval_s = max(fetch_s, TARGET_INTERVAL_S)
                slow = (
                    self.transport == "rest"
                    and fetch_s > TARGET_INTERVAL_S * SLOW_PASS_TOLERANCE
                )
                if slow and not self._warned_slow:
                    self._warned_slow = True
                    logger.warning(
                        f"feed pass takes {fetch_s:.1f}s against a "
                        f"{TARGET_INTERVAL_S:.0f}s target for {len(symbols)} "
                        "symbols. Strategy windows are counted in ticks, so a "
                        f"60-tick window now spans ~{60 * fetch_s:.0f}s while "
                        "time_stop_s stays in real seconds -- positions will "
                        "time out before their window fills. This venue does "
                        "not support watchTickers, so the socket path is "
                        "unavailable; reduce the symbol count or use a venue "
                        "that does."
                    )

                for sym in symbols:
                    t = tickers.get(sym)
                    if not t:
                        continue
                    yield {
                        "ts": now,
                        "symbol": sym,
                        "bid": t.get("bid"),
                        "ask": t.get("ask"),
                        "last": t.get("last"),
                        "volume": t.get("baseVolume"),
                    }

                # The remainder of the target, not a flat second on top of the
                # work, so the cycle aims at a period instead of drifting.
                await asyncio.sleep(
                    max(0.0, TARGET_INTERVAL_S - (time.monotonic() - started))
                )
        finally:
            if watcher is not None:
                watcher.cancel()
                try:
                    await watcher
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            try:
                await ex.close()
            except Exception:
                pass

    async def stop(self) -> None:
        self._running = False
