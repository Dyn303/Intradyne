"""Per-band A1: the cost floor is set by the tick, not by an assumption.

The thing these pin is that `round_trip_bps` is *derived* from price rather
than passed in. `equity_feasibility.py` takes `--spread-bps` as a free
parameter, and 1.0 is fine for a large cap -- but a US equity cannot quote
inside a penny, so below about $20 the parameter has a floor that rises as
price falls. Carrying the 4.3bps large-cap figure downward is the specific
mistake this exists to prevent.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_band_a1 import (  # noqa: E402
    BANDS,
    SELL_FEE_BPS,
    TICK,
    Name,
    measure,
)


def N(price: float, ann_vol: float = 0.20) -> Name:
    """A name at a given price and annualised volatility."""
    per_day = ann_vol / math.sqrt(252)
    return Name(
        symbol="TEST",
        price=price,
        sigma_per_sqrt_s=per_day / math.sqrt(23400),
        bars=2000,
        days=154,
    )


# ---- the cost floor -------------------------------------------------------


def test_one_cent_is_twenty_bps_on_a_five_dollar_stock():
    """The claim the whole script rests on. Spread only, no slippage."""
    spread_only = N(5.0).round_trip_bps(slippage_bps=0.0, spread_ticks=1.0)
    assert spread_only - SELL_FEE_BPS == pytest.approx(20.0, abs=0.01)


def test_the_same_cent_is_five_bps_on_a_twenty_dollar_stock():
    spread_only = N(20.0).round_trip_bps(slippage_bps=0.0, spread_ticks=1.0)
    assert spread_only - SELL_FEE_BPS == pytest.approx(5.0, abs=0.01)


def test_cost_falls_as_one_over_price():
    """Doubling the price halves the spread cost. This is the mechanism."""
    a = N(10.0).round_trip_bps(0.0, 1.0) - SELL_FEE_BPS
    b = N(20.0).round_trip_bps(0.0, 1.0) - SELL_FEE_BPS
    assert a == pytest.approx(2 * b, rel=1e-6)


def test_cost_is_monotonically_worse_for_cheaper_stocks():
    prices = [5.0, 20.0, 50.0, 100.0, 500.0]
    costs = [N(p).round_trip_bps(1.0, 1.0) for p in prices]
    assert costs == sorted(costs, reverse=True)


def test_a_wider_quoted_spread_scales_the_floor():
    one = N(10.0).round_trip_bps(0.0, 1.0) - SELL_FEE_BPS
    four = N(10.0).round_trip_bps(0.0, 4.0) - SELL_FEE_BPS
    assert four == pytest.approx(4 * one, rel=1e-6)


def test_slippage_is_charged_on_both_legs():
    a = N(100.0).round_trip_bps(0.0, 1.0)
    b = N(100.0).round_trip_bps(1.0, 1.0)
    assert b - a == pytest.approx(2.0, abs=1e-6)


def test_sell_fees_are_included_once():
    assert N(100.0).round_trip_bps(0.0, 0.0) == pytest.approx(SELL_FEE_BPS, abs=1e-9)


def test_the_tick_is_a_penny():
    """If this ever changes the whole band analysis moves with it."""
    assert TICK == 0.01


# ---- the move -------------------------------------------------------------


def test_move_scales_with_the_square_root_of_time():
    n = N(100.0)
    assert n.move_bps(4 * 1800) == pytest.approx(2 * n.move_bps(1800), rel=1e-9)


def test_move_is_proportional_to_volatility():
    assert N(100.0, 0.40).move_bps(23400) == pytest.approx(
        2 * N(100.0, 0.20).move_bps(23400), rel=1e-9
    )


def test_move_does_not_depend_on_price():
    """Volatility is a percentage. Only the cost side cares about price, and
    conflating the two is how a cheap stock looks cheaper to trade."""
    assert N(5.0, 0.30).move_bps(23400) == pytest.approx(
        N(500.0, 0.30).move_bps(23400), rel=1e-9
    )


def test_a_twenty_percent_vol_name_moves_about_126bps_a_day():
    # 0.20 / sqrt(252) = 1.26% per session.
    assert N(100.0, 0.20).move_bps(23400) == pytest.approx(126.0, abs=1.0)


# ---- bands ----------------------------------------------------------------


@pytest.mark.parametrize(
    "price,expected",
    [
        (5.0, "$5-20"),
        (19.99, "$5-20"),
        (20.0, "$20-50"),
        (49.99, "$20-50"),
        (50.0, "$50-100"),
        (100.0, "$100-200"),
        (200.0, "$200+"),
        (5000.0, "$200+"),
    ],
)
def test_band_boundaries_are_half_open(price, expected):
    assert N(price).band() == expected


def test_sub_five_dollar_stocks_are_out_of_scope():
    """Below $5 the tick alone exceeds 20bps and the bands stop rather than
    pretending to cover it."""
    assert N(4.99).band() is None


def test_bands_do_not_overlap_and_cover_five_upward():
    for (_, lo1, hi1), (_, lo2, _) in zip(BANDS, BANDS[1:]):
        assert hi1 == lo2, "a gap or overlap between bands would silently drop names"
    assert BANDS[0][1] == 5.0


# ---- measurement ----------------------------------------------------------


def test_overnight_gaps_are_excluded(tmp_path):
    """An intraday strategy is flat at the close. Counting the gap would
    inflate the move a trade can actually capture."""
    p = tmp_path / "GAP_30min.csv"
    body = []
    # Each session is flat within itself and the level alternates between
    # days, so every bit of variation in the file sits across an overnight
    # boundary and none of it inside a session.
    for d in range(1, 41):
        level = 100 if d % 2 else 200
        for t in ("09:30:00", "10:00:00", "10:30:00", "11:00:00", "11:30:00"):
            body.append(f"2026-02-{d:02d} {t},{level},{level},{level},{level},1000")
    p.write_text(
        "\n".join(["datetime,open,high,low,close,volume"] + body), encoding="utf-8"
    )
    n = measure(p)
    assert n is not None
    assert n.sigma_per_sqrt_s == pytest.approx(0.0, abs=1e-12), (
        "the only variation in this file is overnight; sigma must be zero"
    )


def test_a_file_with_too_little_history_is_skipped(tmp_path):
    p = tmp_path / "TINY_30min.csv"
    p.write_text(
        "datetime,open,high,low,close,volume\n2026-01-05 09:30:00,10,10,10,10,1\n",
        encoding="utf-8",
    )
    assert measure(p) is None


# ---- breakeven spread -----------------------------------------------------
#
# Every ratio in this script assumes a one-tick spread, which is the tightest a
# US equity may legally quote. That makes each band pass more easily than it
# should, so the load-bearing number is not the ratio but how far the spread
# would have to widen before the band failed.


def _breakeven_ticks(move_bps: float, price: float, slippage: float) -> float:
    """Closed form used by the script: rt(k) = 100k/P + 2*slip + fees."""
    return (move_bps - 2.0 * slippage - SELL_FEE_BPS) * price / 100.0


def test_breakeven_spread_matches_the_cost_model_it_inverts():
    """Widening the spread to the breakeven must drive the round trip to the
    move -- the inverse and the forward model have to agree."""
    n = Name(symbol="X", price=11.30, sigma_per_sqrt_s=0.0, bars=0, days=0)
    move = 300.0
    k = _breakeven_ticks(move, n.price, slippage=1.0)
    assert n.round_trip_bps(slippage_bps=1.0, spread_ticks=k) == pytest.approx(
        move, rel=1e-9
    )


def test_a_cheap_band_still_needs_a_spread_far_wider_than_a_penny():
    """The result the gate turns on: an $11 share moving ~300bps a day would
    need roughly a third of a dollar of spread before the move stopped paying
    for it, against real spreads of one to three cents."""
    k = _breakeven_ticks(300.0, 11.30, slippage=1.0)
    assert k > 30
    assert k * TICK > 0.30


def test_breakeven_rises_with_price_at_equal_move():
    """A fixed proportional move is worth more cents on a dearer share, so the
    spread it can absorb is larger."""
    cheap = _breakeven_ticks(300.0, 10.0, slippage=1.0)
    dear = _breakeven_ticks(300.0, 300.0, slippage=1.0)
    assert dear > cheap


def test_a_move_below_slippage_and_fees_can_never_break_even():
    """No spread, however tight, rescues a move smaller than the costs that do
    not depend on the spread at all."""
    assert _breakeven_ticks(1.0, 50.0, slippage=1.0) <= 0
