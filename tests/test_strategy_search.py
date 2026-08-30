"""The screen's conclusion rests on its exit simulation and its null
threshold being right, so both are pinned here."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

pytest.importorskip("pandas")

from strategy_search import (  # noqa: E402
    Bars,
    evaluate,
    forward_outcomes,
    build_strategies,
    null_threshold,
)


def _bars(closes, highs=None, lows=None):
    c = np.array(closes, dtype=float)
    h = np.array(highs, dtype=float) if highs is not None else c.copy()
    lo = np.array(lows, dtype=float) if lows is not None else c.copy()
    n = len(c)
    return Bars(
        ts=np.arange(n, dtype=float),
        open=c,
        high=h,
        low=lo,
        close=c,
        volume=np.ones(n),
        buy_volume=np.ones(n),
        sell_volume=np.ones(n),
        trades=np.ones(n),
    )


# ---- exit simulation -----------------------------------------------------


def test_reaching_the_target_pays_the_target_less_costs():
    bars = _bars([100.0, 100.0, 101.0])  # +100bps on the third bar
    net, held = forward_outcomes(bars, tp_bps=40, sl_bps=20, horizon=5, cost_bps=14)
    assert net[0] == pytest.approx(40 - 14)
    assert held[0] == 2


def test_reaching_the_stop_pays_the_stop_plus_costs():
    bars = _bars([100.0, 99.0])
    net, _ = forward_outcomes(bars, tp_bps=40, sl_bps=20, horizon=5, cost_bps=14)
    assert net[0] == pytest.approx(-20 - 14)


def test_a_bar_spanning_both_is_scored_as_the_stop():
    """Bar data cannot say which came first, so the pessimistic reading is
    the only defensible one. Scoring it as a win would manufacture edge."""
    bars = _bars([100.0, 100.0], highs=[100.0, 101.0], lows=[100.0, 99.0])
    net, _ = forward_outcomes(bars, tp_bps=40, sl_bps=20, horizon=5, cost_bps=0)
    assert net[0] == pytest.approx(-20)


def test_an_untouched_position_exits_at_the_horizon():
    bars = _bars([100.0, 100.05, 100.1, 100.1])
    net, held = forward_outcomes(bars, tp_bps=40, sl_bps=40, horizon=2, cost_bps=0)
    assert net[0] == pytest.approx(10.0, abs=0.1)  # 100 -> 100.1 is +10bps
    assert held[0] == 2


def test_costs_are_charged_on_every_trade():
    """A signal with no predictive power must land near minus the round-trip
    cost, not near zero -- otherwise the screen flatters everything."""
    rng = np.random.default_rng(0)
    walk = 100.0 * np.exp(np.cumsum(rng.normal(0, 1e-4, 20_000)))
    net, _ = forward_outcomes(_bars(walk), 40, 40, 300, cost_bps=14)
    assert np.nanmean(net) < -10.0


# ---- trade selection -----------------------------------------------------


def test_trades_cannot_overlap():
    """Without this a signal that fires on every bar books the same move
    hundreds of times and reports a fortune."""
    net = np.zeros(100)
    held = np.full(100, 10)
    always_on = np.ones(100, dtype=bool)
    assert evaluate(always_on, net, held, min_gap=1)["trades"] == 10


def test_a_signal_that_never_fires_reports_no_trades():
    assert (
        evaluate(np.zeros(50, dtype=bool), np.zeros(50), np.ones(50, dtype=int), 1)[
            "trades"
        ]
        == 0
    )


# ---- the selection-bias guard -------------------------------------------


def test_best_of_many_random_signals_beats_the_average_signal():
    """The reason the screen needs a null threshold at all: the maximum of N
    noisy estimates sits well above the mean even when none of them has an
    edge. Ranking 50 strategies and taking the top is exactly this draw."""
    rng = np.random.default_rng(3)
    net = rng.normal(-14.0, 60.0, 50_000)
    held = np.ones(50_000, dtype=int)
    thr = null_threshold(
        net, held, [200], n_strategies=50, draws=200, rng=rng, min_gap=1
    )
    assert thr > float(net.mean()) + 5.0


def test_the_library_offers_many_distinct_signals():
    strategies = build_strategies()
    assert len(strategies) >= 50
    families = {name.rsplit("_", 1)[0].split("_")[0] for name in strategies}
    assert len(families) >= 8, f"only {families}"
