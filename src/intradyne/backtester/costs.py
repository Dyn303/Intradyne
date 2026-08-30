"""Cost-aware edge analysis.

A win rate on its own says nothing about whether a scalping strategy makes
money. At a 20bps take-profit against a 30bps stop, round-trip costs of 14bps
mean a winner nets 6bps while a loser costs 44bps -- so the strategy needs to
win roughly 88% of the time merely to break even. A 70% win rate looks strong
and loses money.

Every backtest summary therefore reports the measured win rate *next to* the
breakeven win rate implied by its own fee and exit settings, so the two cannot
be read apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: How much better than breakeven a result should be before it is treated as
#: evidence of an edge rather than noise. Breakeven plus a rounding error is
#: not a strategy.
DEFAULT_REQUIRED_MARGIN = 0.05


def round_trip_cost_pct(
    taker_bps: float,
    slippage_bps: float,
    maker_bps: float = 0.0,
    maker_entry: bool = False,
    maker_exit: bool = False,
) -> float:
    """Total round-trip cost as a fraction of notional.

    Slippage applies only to the taker legs: a resting limit order is filled
    at its own price, so it pays the maker fee and no slippage.
    """
    entry = (
        maker_bps / 10_000.0 if maker_entry else (taker_bps + slippage_bps) / 10_000.0
    )
    exit_ = (
        maker_bps / 10_000.0 if maker_exit else (taker_bps + slippage_bps) / 10_000.0
    )
    return entry + exit_


def breakeven_win_rate(
    tp_pct: float, sl_pct: float, cost_pct: float
) -> Optional[float]:
    """Win rate at which expectancy is zero.

    Solves ``p * (tp - cost) = (1 - p) * (sl + cost)``. Returns None when no
    win rate can break even -- which happens when costs exceed the entire
    take-profit, so every "winning" trade still loses money.
    """
    net_win = tp_pct - cost_pct
    net_loss = sl_pct + cost_pct
    if net_win <= 0:
        return None
    denominator = net_win + net_loss
    if denominator <= 0:
        return None
    return net_loss / denominator


def expectancy_pct(
    win_rate: float, tp_pct: float, sl_pct: float, cost_pct: float
) -> float:
    """Expected return per trade, as a fraction of notional, after costs."""
    return win_rate * (tp_pct - cost_pct) - (1.0 - win_rate) * (sl_pct + cost_pct)


@dataclass
class EdgeAssessment:
    win_rate: float
    breakeven_win_rate: Optional[float]
    margin: Optional[float]
    cost_pct: float
    net_win_pct: float
    net_loss_pct: float
    expectancy_pct: float
    verdict: str
    note: str

    def to_dict(self) -> dict:
        return {
            "win_rate": self.win_rate,
            "breakeven_win_rate": self.breakeven_win_rate,
            "margin": self.margin,
            "round_trip_cost_pct": self.cost_pct,
            "net_win_pct": self.net_win_pct,
            "net_loss_pct": self.net_loss_pct,
            "expectancy_pct": self.expectancy_pct,
            "verdict": self.verdict,
            "note": self.note,
        }


def assess(
    win_rate: float,
    tp_pct: float,
    sl_pct: float,
    taker_bps: float,
    slippage_bps: float,
    maker_bps: float = 0.0,
    maker_entry: bool = False,
    maker_exit: bool = False,
    required_margin: float = DEFAULT_REQUIRED_MARGIN,
    trades: int = 0,
) -> EdgeAssessment:
    """Compare a measured win rate against the breakeven it must clear."""
    cost = round_trip_cost_pct(
        taker_bps, slippage_bps, maker_bps, maker_entry, maker_exit
    )
    breakeven = breakeven_win_rate(tp_pct, sl_pct, cost)
    net_win = tp_pct - cost
    net_loss = sl_pct + cost

    if breakeven is None:
        return EdgeAssessment(
            win_rate=win_rate,
            breakeven_win_rate=None,
            margin=None,
            cost_pct=cost,
            net_win_pct=net_win,
            net_loss_pct=net_loss,
            expectancy_pct=expectancy_pct(win_rate, tp_pct, sl_pct, cost),
            verdict="impossible",
            note=(
                f"round-trip cost {cost * 1e4:.1f}bps meets or exceeds the "
                f"{tp_pct * 1e4:.1f}bps take-profit, so even a winning trade "
                "loses money. No win rate can break even."
            ),
        )

    margin = win_rate - breakeven
    exp = expectancy_pct(win_rate, tp_pct, sl_pct, cost)

    if trades and trades < 100:
        verdict = "insufficient_data"
        note = f"only {trades} trades; too few to distinguish edge from noise"
    elif margin >= required_margin:
        verdict = "clears_with_margin"
        note = (
            f"win rate {win_rate:.1%} clears breakeven {breakeven:.1%} by {margin:.1%}"
        )
    elif margin > 0:
        verdict = "marginal"
        note = (
            f"win rate {win_rate:.1%} clears breakeven {breakeven:.1%} by only "
            f"{margin:.1%}; inside the range a small cost or fill-quality "
            "change would erase"
        )
    else:
        verdict = "below_breakeven"
        note = (
            f"win rate {win_rate:.1%} is below breakeven {breakeven:.1%}; "
            "this loses money after costs"
        )

    return EdgeAssessment(
        win_rate=win_rate,
        breakeven_win_rate=breakeven,
        margin=margin,
        cost_pct=cost,
        net_win_pct=net_win,
        net_loss_pct=net_loss,
        expectancy_pct=exp,
        verdict=verdict,
        note=note,
    )


def profit_factor(
    win_rate: float, tp_pct: float, sl_pct: float, cost_pct: float
) -> Optional[float]:
    """Profit factor implied by a win rate and payoff geometry.

    Gross profit over gross loss. This is the same statement as expectancy:
    PF > 1 exactly when expectancy > 0, and PF == 1 at breakeven. Raising it
    means either winning more often or changing the geometry -- there is no
    third lever.
    """
    net_win = tp_pct - cost_pct
    net_loss = sl_pct + cost_pct
    losses = (1.0 - win_rate) * net_loss
    if losses <= 0:
        return None
    return (win_rate * net_win) / losses


def frontier(
    tp_grid_bps,
    sl_grid_bps,
    cost_pct: float,
):
    """Breakeven win rate for each (take-profit, stop-loss) pair.

    The point of looking at this: at a 20bps target against a 30bps stop with
    taker exits, breakeven is 88% -- a win rate essentially no entry signal
    reaches. Widening the target to 60bps drops it to 49%. The bar is set by
    the geometry far more than by signal quality, so tuning entries against an
    unreachable bar is wasted effort.
    """
    rows = []
    for tp in tp_grid_bps:
        row = []
        for sl in sl_grid_bps:
            row.append(breakeven_win_rate(tp / 1e4, sl / 1e4, cost_pct))
        rows.append((tp, row))
    return rows


__all__ = [
    "DEFAULT_REQUIRED_MARGIN",
    "frontier",
    "profit_factor",
    "EdgeAssessment",
    "assess",
    "breakeven_win_rate",
    "expectancy_pct",
    "round_trip_cost_pct",
]
