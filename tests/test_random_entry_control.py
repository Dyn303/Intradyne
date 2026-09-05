"""The Stage 2 control arm.

Stage 2 asks whether the entry rule's moments differ from other moments. That
needs a comparison, and "nothing" is not one. The control enters at random
times and holds under identical rules, so any difference is attributable to
*when* it entered and to nothing else.
"""

from __future__ import annotations


from intradyne.core.config import Settings
from intradyne.engine.strategies.random_entry import RandomEntryStrategy


def _q(px=100.0):
    return {"symbol": "BTC/USDT", "bid": px, "ask": px, "last": px, "ts": 1.0}


def _fires(p, n=20_000, seed=0, sym="BTC/USDT"):
    s = RandomEntryStrategy(symbol=sym, p=p, seed=seed)
    return sum(1 for _ in range(n) if s.on_tick(_q()))


class TestRate:
    def test_it_fires_at_about_the_configured_rate(self):
        n = 20_000
        got = _fires(0.004, n)
        assert abs(got / n - 0.004) < 0.0015, f"{got}/{n}"

    def test_zero_never_fires(self):
        assert _fires(0.0) == 0

    def test_one_always_fires(self):
        assert _fires(1.0, n=50) == 50


class TestIndependence:
    def test_two_symbols_do_not_fire_in_lockstep(self):
        """Seeded per symbol. A shared stream would make every symbol enter on
        the same ticks, manufacturing correlation the real strategy does not
        have and shrinking the effective sample."""
        a = RandomEntryStrategy(symbol="BTC/USDT", p=0.5, seed=7)
        b = RandomEntryStrategy(symbol="ETH/USDT", p=0.5, seed=7)
        agree = sum(
            1 for _ in range(2000) if bool(a.on_tick(_q())) == bool(b.on_tick(_q()))
        )
        assert 0.4 < agree / 2000 < 0.6, "symbols are not independent"

    def test_the_same_seed_repeats(self):
        assert _fires(0.05, seed=3) == _fires(0.05, seed=3)

    def test_a_different_seed_differs(self):
        assert _fires(0.05, seed=3) != _fires(0.05, seed=4)


class TestItIgnoresTheMarket:
    """The property that makes it a control rather than a strategy."""

    def test_the_price_does_not_change_the_rate(self):
        s = RandomEntryStrategy(symbol="BTC/USDT", p=0.5, seed=1)
        rising = sum(1 for i in range(4000) if s.on_tick(_q(100.0 + i)))
        s2 = RandomEntryStrategy(symbol="BTC/USDT", p=0.5, seed=1)
        falling = sum(1 for i in range(4000) if s2.on_tick(_q(100.0 - i * 0.01)))
        assert rising == falling, "the control is reading the market"

    def test_a_tick_with_no_price_is_skipped(self):
        """The real strategies return on this too, so the control must see
        the same ticks or the comparison is against a different sample."""
        s = RandomEntryStrategy(symbol="BTC/USDT", p=1.0, seed=1)
        assert s.on_tick({"symbol": "BTC/USDT", "ts": 1.0}) is None

    def test_the_signal_is_shaped_like_a_real_one(self):
        s = RandomEntryStrategy(symbol="BTC/USDT", p=1.0, seed=1)
        sig = s.on_tick(_q())
        assert sig is not None
        assert sig["action"] == "buy"
        assert sig["reason"] == "random_control"


def test_the_control_is_off_by_default():
    """It disables the real strategies, so it must never be on by accident."""
    assert Settings().random_entry_p == 0.0


def test_expected_trade_count_is_plausible_for_a_days_run():
    """Sanity on the p that will actually be used: at a 1s interval over six
    symbols, p=0.004 is a few hundred signals a day before capacity limits
    refuse some -- the same order as the live strategy's rate."""
    ticks_per_symbol_per_hour = 3600
    symbols, hours = 6, 8
    expected = 0.004 * ticks_per_symbol_per_hour * symbols * hours
    assert 400 < expected < 1200, expected
