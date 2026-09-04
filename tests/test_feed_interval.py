"""The feed's tick interval, which is also the unit of every strategy window.

`MomentumStrategy` keeps `deque(maxlen=120)` and a `breakout_window` of 60.
Both count *ticks*. `router.py:231` stops a position on `now - entry >=
time_stop_s`, which counts *seconds*. Nothing reconciles the two except the
feed's cadence, so these tests are about a strategy invariant as much as
about a data source.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from intradyne.engine.data_ws import (
    SLOW_PASS_TOLERANCE,
    TARGET_INTERVAL_S,
    DataFeed,
)


SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"]


class FakeExchange:
    """A venue with a fixed per-call latency, so cost is countable."""

    def __init__(self, latency_s: float = 0.02, batched: bool = True) -> None:
        self.has = {"fetchTickers": batched}
        self.latency_s = latency_s
        self.single_calls = 0
        self.batch_calls = 0

    async def load_markets(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        self.single_calls += 1
        await asyncio.sleep(self.latency_s)
        return {"bid": 1.0, "ask": 1.1, "last": 1.05, "baseVolume": 10.0}

    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Any]:
        self.batch_calls += 1
        await asyncio.sleep(self.latency_s)
        return {
            s: {"bid": 1.0, "ask": 1.1, "last": 1.05, "baseVolume": 10.0}
            for s in symbols
        }


async def _drain(feed: DataFeed, ticks: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    async for tick in feed.start(SYMBOLS):
        out.append(tick)
        if len(out) >= ticks:
            await feed.stop()
            break
    return out


def _run(feed: DataFeed, ex: FakeExchange, ticks: int) -> List[Dict[str, Any]]:
    feed.exchange = ex  # bypass the ccxt constructor
    return asyncio.run(_drain(feed, ticks))


def test_one_call_serves_every_symbol():
    """The defect this fixes: cost scaled with symbol count, so adding a
    symbol silently changed how much time a 60-tick window covered."""
    ex = FakeExchange()
    feed = DataFeed(exchange_id="kraken")
    ticks = _run(feed, ex, len(SYMBOLS))

    assert ex.batch_calls == 1, "one pass should cost one request"
    assert ex.single_calls == 0
    assert {t["symbol"] for t in ticks} == set(SYMBOLS)


def test_every_symbol_in_a_pass_shares_a_timestamp():
    """A pass is one observation of the market. Sequential polling gave the
    last symbol a view N x latency older than the first."""
    ex = FakeExchange()
    feed = DataFeed(exchange_id="kraken")
    ticks = _run(feed, ex, len(SYMBOLS))
    assert len({t["ts"] for t in ticks}) == 1


def test_interval_is_a_period_not_an_addition():
    """The old loop slept a flat second *after* the work, making the interval
    `latency + 1s`. The sleep is now the remainder, so a fast venue lands on
    the target rather than overshooting it."""
    ex = FakeExchange(latency_s=0.05)
    feed = DataFeed(exchange_id="kraken")

    started = time.monotonic()
    _run(feed, ex, len(SYMBOLS) * 2)  # two full passes
    elapsed = time.monotonic() - started

    # One target interval separates the two passes; the old code would have
    # taken TARGET + latency for the same span.
    assert elapsed < TARGET_INTERVAL_S + ex.latency_s * 2
    assert feed.interval_s is not None
    assert feed.interval_s <= TARGET_INTERVAL_S + 0.05


def test_slow_venue_reports_its_interval_rather_than_hiding_it():
    """When the round trip exceeds the target the feed cannot deliver it.
    That is a fact about the venue, so it is reported, not silently absorbed:
    `interval_s` is what a strategy window is actually denominated in."""
    ex = FakeExchange(latency_s=TARGET_INTERVAL_S * 2.5)
    feed = DataFeed(exchange_id="kraken")
    _run(feed, ex, len(SYMBOLS))

    assert feed.interval_s is not None
    assert feed.interval_s >= TARGET_INTERVAL_S * 2.5
    assert feed._warned_slow, "a stretched window must not pass unremarked"


def test_ordinary_jitter_does_not_trip_the_warning():
    """Kraken's batched call measures 1.0-1.2s against a 1.0s target. A strict
    comparison would warn at every launch, and a warning that always fires is
    one nobody reads."""
    ex = FakeExchange(latency_s=TARGET_INTERVAL_S * (SLOW_PASS_TOLERANCE - 0.2))
    feed = DataFeed(exchange_id="kraken")
    _run(feed, ex, len(SYMBOLS))
    assert not feed._warned_slow


def test_falls_back_when_the_venue_has_no_batch_endpoint():
    ex = FakeExchange(batched=False)
    feed = DataFeed(exchange_id="kraken")
    ticks = _run(feed, ex, len(SYMBOLS))

    assert ex.batch_calls == 0
    assert ex.single_calls == len(SYMBOLS)
    assert {t["symbol"] for t in ticks} == set(SYMBOLS)


class WSExchange(FakeExchange):
    """A venue that pushes fast, the way Bitget does at ~9.9 updates/sec."""

    def __init__(self, push_gap_s: float = 0.01, fail_after: int | None = None) -> None:
        super().__init__(latency_s=0.02, batched=True)
        self.has = {"fetchTickers": True, "watchTickers": True}
        self.push_gap_s = push_gap_s
        self.pushes = 0
        self.fail_after = fail_after

    async def watch_tickers(self, symbols):
        await asyncio.sleep(self.push_gap_s)
        self.pushes += 1
        if self.fail_after is not None and self.pushes > self.fail_after:
            raise ConnectionError("socket dropped")
        return {
            s: {"bid": 1.0, "ask": 1.1, "last": 1.05, "baseVolume": 10.0}
            for s in symbols
        }


def test_the_socket_is_used_when_the_venue_offers_it():
    ex = WSExchange()
    feed = DataFeed(exchange_id="bitget")
    _run(feed, ex, len(SYMBOLS) * 2)
    assert feed.transport == "websocket"
    assert ex.pushes > 0


def test_a_fast_socket_does_not_speed_up_the_tick_rate():
    """The defect this design avoids. Bitget pushes ~9.9 updates a second;
    emitting on each would take a 60-tick window from 60s to 6s -- the same
    failure as a slow feed, inverted. Cadence belongs to the emitter.
    """
    ex = WSExchange(push_gap_s=0.001)  # ~1000 pushes/sec
    feed = DataFeed(exchange_id="bitget")

    started = time.monotonic()
    _run(feed, ex, len(SYMBOLS) * 2)  # two passes
    elapsed = time.monotonic() - started

    assert elapsed >= TARGET_INTERVAL_S * 0.9, (
        f"two passes took {elapsed:.2f}s -- emission is following the socket"
    )
    assert feed.interval_s is not None
    assert feed.interval_s <= TARGET_INTERVAL_S + 0.2
    assert ex.pushes > 10, "the socket should still be pushing hard underneath"


def test_the_socket_saves_the_round_trip():
    """Once quotes are flowing there is nothing to fetch."""
    ex = WSExchange()
    feed = DataFeed(exchange_id="bitget")
    _run(feed, ex, len(SYMBOLS) * 3)
    assert ex.batch_calls <= 1, "REST covered only the gap before first push"


def test_no_slow_warning_on_the_socket_path():
    """There is no round trip to be slow, so the REST warning must not fire
    and send someone chasing latency that is not there."""
    ex = WSExchange()
    feed = DataFeed(exchange_id="bitget")
    _run(feed, ex, len(SYMBOLS) * 2)
    assert not feed._warned_slow


def test_a_dropped_socket_discards_its_quotes():
    """Stale quotes are worse than none: pricing an order off a market that
    has moved is a real loss, where a REST round trip is only a delay."""
    ex = WSExchange(fail_after=1)
    feed = DataFeed(exchange_id="bitget")
    _run(feed, ex, len(SYMBOLS) * 3)
    assert ex.batch_calls >= 1, "REST should have covered the gap"


def test_a_venue_without_watch_tickers_still_polls():
    ex = FakeExchange()  # advertises fetchTickers only
    feed = DataFeed(exchange_id="kraken")
    ticks = _run(feed, ex, len(SYMBOLS))
    assert feed.transport == "rest"
    assert ex.batch_calls >= 1
    assert {t["symbol"] for t in ticks} == set(SYMBOLS)
