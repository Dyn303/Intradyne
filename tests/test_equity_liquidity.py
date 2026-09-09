"""The liquidity floor must be point-in-time, and must not be fooled.

Two properties carry this, and both fail silently:

  Only data at or before the rebalance date may affect the verdict. A floor
  computed from today's volume decides what was tradeable in 2019 using
  information from 2026.

  "Illiquid" and "unknowable" are different answers. This provider serves no
  delisted history, and one of the two ways it fails is a flat line at zero
  volume rather than an error -- see the ADVM case below. Scored as data, that
  is a name with no volatility and no turnover; recorded as a liquidity
  failure, it turns a survivorship hole into a finding about the stock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from equity_liquidity import (  # noqa: E402
    MIN_OBSERVATIONS,
    Series,
    judge,
    parse_daily,
)


def series(symbol="X", n=120, close=10.0, volume=500_000.0, start="2026-01-01"):
    """A well-behaved daily series ending 2026-06-30 by default."""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    s = Series(symbol=symbol)
    for i in range(n):
        s.dates.append((d0 + timedelta(days=i)).isoformat())
        s.close.append(close if not callable(close) else close(i))
        s.volume.append(volume if not callable(volume) else volume(i))
    return s


AS_OF = "2026-04-20"  # ~110 days after 2026-01-01, inside the default window


# ---- the ADVM shape: a placeholder, not a stock --------------------------


def test_a_flat_line_at_zero_volume_is_not_data():
    """ADVM delisted 2026-09-02 and the provider returns 100 sessions of
    4.3600 at volume 0. This must never be scored."""
    s = series(volume=0.0, close=4.36)
    v = judge(s, "ADVM", AS_OF)
    assert v.usable is False
    assert v.passed is False
    assert "no_data" in v.reason


def test_zero_volume_alone_is_enough_to_reject():
    s = series(volume=0.0, close=lambda i: 10.0 + i * 0.01)
    v = judge(s, "X", AS_OF)
    assert v.usable is False and "zero volume" in v.reason


def test_a_constant_price_alone_is_enough_to_reject():
    s = series(close=10.0, volume=5_000_000.0)
    v = judge(s, "X", AS_OF)
    assert v.usable is False and "one price" in v.reason


def test_a_missing_series_is_unknowable_not_illiquid():
    v = judge(None, "FXEN", AS_OF)
    assert v.usable is False
    assert v.passed is False
    assert "no_data" in v.reason


# ---- illiquid and unknowable are different answers ------------------------


def test_a_real_but_thin_name_fails_on_the_merits():
    """The distinction the whole script turns on: this one IS judgeable."""
    s = series(close=lambda i: 10.0 + (i % 7) * 0.1, volume=100.0)
    v = judge(s, "THIN", AS_OF, min_dollar_volume=1_000_000.0)
    assert v.usable is True, "a real series must be judgeable"
    assert v.passed is False
    assert "below floor" in v.reason


def test_a_liquid_name_clears():
    s = series(close=lambda i: 10.0 + (i % 5) * 0.2, volume=1_000_000.0)
    v = judge(s, "AAPL", AS_OF, min_dollar_volume=1_000_000.0)
    assert v.usable is True and v.passed is True
    assert v.dollar_volume is not None and v.dollar_volume > 1_000_000.0


# ---- point-in-time --------------------------------------------------------


def test_volume_after_the_rebalance_date_is_ignored():
    """The property that makes this A3 rather than a screen. A name that was
    thin then and is heavily traded now must still fail then."""

    def vol(i):
        return 100.0 if i < 110 else 50_000_000.0

    s = series(n=200, close=lambda i: 10.0 + (i % 5) * 0.1, volume=vol)
    v = judge(s, "X", AS_OF, min_dollar_volume=1_000_000.0)
    assert v.passed is False, "future volume leaked into a past verdict"


def test_the_same_name_can_pass_later_and_fail_earlier():
    def vol(i):
        return 100.0 if i < 110 else 50_000_000.0

    s = series(n=250, close=lambda i: 10.0 + (i % 5) * 0.1, volume=vol)
    early = judge(s, "X", AS_OF, min_dollar_volume=1_000_000.0)
    late = judge(s, "X", "2026-08-01", min_dollar_volume=1_000_000.0)
    assert early.passed is False and late.passed is True


def test_window_excludes_bars_after_as_of():
    s = series(n=200)
    close, vol = s.window(AS_OF, 90)
    assert close.size > 0
    assert all(d <= AS_OF for d in s.dates[: close.size])


# ---- staleness and sample size --------------------------------------------


def test_a_name_that_stopped_trading_before_as_of_is_unknowable():
    s = series(n=60, start="2025-01-01")  # ends long before AS_OF
    v = judge(s, "DEAD", AS_OF)
    assert v.usable is False and "before as_of" in v.reason


def test_too_few_observations_is_unknowable_not_a_pass():
    s = series(n=MIN_OBSERVATIONS - 5, start="2026-03-25")
    v = judge(s, "NEW", "2026-04-20")
    assert v.usable is False and "bars in window" in v.reason


# ---- the measure ----------------------------------------------------------


def test_median_not_mean_so_one_huge_day_cannot_carry_a_name():
    """A name that trades in size once and not otherwise is not liquid."""

    def vol(i):
        return 5_000_000_000.0 if i == 100 else 100.0

    s = series(close=lambda i: 10.0 + (i % 5) * 0.1, volume=vol)
    v = judge(s, "SPIKE", AS_OF, min_dollar_volume=1_000_000.0)
    assert v.usable is True
    assert v.passed is False, "a single huge day carried the estimate"


def test_dollar_volume_is_price_times_shares():
    s = series(close=lambda i: 20.0 + (i % 3), volume=100_000.0)
    v = judge(s, "X", AS_OF, min_dollar_volume=0.0)
    assert v.dollar_volume == pytest.approx(21.0 * 100_000.0, rel=0.2)


def test_the_floor_is_configurable():
    # ~10.2 x 50,000 = ~$510k/day: above a $100k floor, below a $1M one.
    s = series(close=lambda i: 10.0 + (i % 5) * 0.1, volume=50_000.0)
    assert judge(s, "X", AS_OF, min_dollar_volume=1_000_000.0).passed is False
    assert judge(s, "X", AS_OF, min_dollar_volume=100_000.0).passed is True


# ---- parsing --------------------------------------------------------------


def test_parse_orders_oldest_first_whatever_the_provider_sends():
    text = (
        "timestamp,open,high,low,close,volume\n"
        "2026-03-03,1,1,1,3.0,300\n"
        "2026-03-01,1,1,1,1.0,100\n"
        "2026-03-02,1,1,1,2.0,200\n"
    )
    s = parse_daily(text, "X")
    assert s.dates == ["2026-03-01", "2026-03-02", "2026-03-03"]
    assert s.close == [1.0, 2.0, 3.0]


def test_parse_skips_malformed_rows_rather_than_failing():
    text = (
        "timestamp,open,high,low,close,volume\n"
        "2026-03-01,1,1,1,1.0,100\n"
        "2026-03-02,1,1,1,,\n"
        "2026-03-03,1,1,1,3.0,300\n"
    )
    s = parse_daily(text, "X")
    assert s.dates == ["2026-03-01", "2026-03-03"]
