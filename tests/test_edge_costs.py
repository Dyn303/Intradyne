"""Cost-aware edge assessment.

A win rate read without its breakeven is meaningless for a scalper: at the
shipped defaults a 70% win rate looks strong and loses money.
"""

from __future__ import annotations

import pytest

from intradyne.backtester.costs import (
    assess,
    breakeven_win_rate,
    expectancy_pct,
    round_trip_cost_pct,
)


# Shipped defaults: TP_PCT=0.002, PER_TRADE_SL_PCT=0.003, taker 5bps, slip 2bps.
TP = 0.002
SL = 0.003
TAKER = 5.0
SLIP = 2.0


def test_round_trip_cost_at_shipped_defaults():
    """7bps a side, both legs taker."""
    assert round_trip_cost_pct(TAKER, SLIP) == pytest.approx(0.0014)


def test_maker_fills_cost_less_and_pay_no_slippage():
    """A resting limit order fills at its own price."""
    maker_both = round_trip_cost_pct(
        TAKER, SLIP, maker_bps=2.0, maker_entry=True, maker_exit=True
    )
    assert maker_both == pytest.approx(0.0004)
    assert maker_both < round_trip_cost_pct(TAKER, SLIP)


def test_breakeven_at_shipped_defaults_is_about_88_percent():
    """The number that motivated this module: a winner nets 6bps against a
    44bps loser, so ~88% of trades must win merely to break even."""
    be = breakeven_win_rate(TP, SL, round_trip_cost_pct(TAKER, SLIP))
    assert be == pytest.approx(0.88, abs=0.005)


def test_all_maker_fills_lower_the_bar_substantially():
    cost = round_trip_cost_pct(
        TAKER, SLIP, maker_bps=2.0, maker_entry=True, maker_exit=True
    )
    be = breakeven_win_rate(TP, SL, cost)
    assert be == pytest.approx(0.68, abs=0.01)


def test_costs_exceeding_the_take_profit_make_breakeven_impossible():
    """When a 'winning' trade still loses money, no win rate saves it."""
    assert breakeven_win_rate(tp_pct=0.0010, sl_pct=0.003, cost_pct=0.0014) is None


def test_zero_cost_breakeven_is_the_naive_ratio():
    """Sanity anchor: with no costs, 30bps risked for 20bps needs 60%."""
    assert breakeven_win_rate(TP, SL, 0.0) == pytest.approx(0.6)


def test_expectancy_is_zero_at_breakeven():
    cost = round_trip_cost_pct(TAKER, SLIP)
    be = breakeven_win_rate(TP, SL, cost)
    assert expectancy_pct(be, TP, SL, cost) == pytest.approx(0.0, abs=1e-12)


# ---- the verdicts --------------------------------------------------------


def _assess(win_rate, trades=1000, **kw):
    return assess(
        win_rate=win_rate,
        tp_pct=TP,
        sl_pct=SL,
        taker_bps=TAKER,
        slippage_bps=SLIP,
        trades=trades,
        **kw,
    )


def test_a_strong_looking_win_rate_still_loses_money():
    """70% reads as a good strategy and is well below the 88% it must clear."""
    result = _assess(0.70)
    assert result.verdict == "below_breakeven"
    assert result.expectancy_pct < 0


def test_barely_clearing_breakeven_is_flagged_as_marginal():
    """The user's requirement: clearing it barely does not count."""
    result = _assess(0.89)
    assert result.verdict == "marginal"
    assert result.expectancy_pct > 0


def test_clearing_with_margin_is_the_only_pass():
    result = _assess(0.95)
    assert result.verdict == "clears_with_margin"
    assert result.margin >= 0.05


def test_too_few_trades_is_not_evidence_either_way():
    """A 95% win rate over 12 trades is noise, not an edge."""
    assert _assess(0.95, trades=12).verdict == "insufficient_data"


def test_impossible_costs_are_reported_as_such():
    result = assess(
        win_rate=0.99,
        tp_pct=0.0010,
        sl_pct=0.003,
        taker_bps=TAKER,
        slippage_bps=SLIP,
        trades=1000,
    )
    assert result.verdict == "impossible"
    assert result.breakeven_win_rate is None


def test_assessment_serialises_for_the_backtest_summary():
    body = _assess(0.9).to_dict()
    for key in (
        "win_rate",
        "breakeven_win_rate",
        "margin",
        "round_trip_cost_pct",
        "expectancy_pct",
        "verdict",
        "note",
    ):
        assert key in body
