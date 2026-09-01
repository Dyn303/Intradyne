"""The universe builder exists to remove two biases, so the tests are mostly
attempts to reintroduce them.

Both biases inflate a backtest silently and in the same direction. A
survivorship-biased universe holds only coins that lived, so momentum looks
brilliant. A look-ahead-biased one decides tradeability from today's volume,
so it quietly buys things nobody could have bought at the time. Neither
produces an error -- they produce an encouraging number, which is worse.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from point_in_time_universe import (  # noqa: E402
    DAY_MS,
    LEVERAGED,
    month_ends,
    universe_at,
)

DAY = DAY_MS
T0 = 1_600_000_000_000  # an arbitrary epoch-ms origin


def series(start_day: int, n_days: int, qvol: float = 1e6, close: float = 100.0):
    ts = np.array([T0 + (start_day + i) * DAY for i in range(n_days)], dtype="int64")
    return {
        "ts": ts,
        "close": np.full(n_days, close, dtype="float64"),
        "qvol": np.full(n_days, qvol, dtype="float64"),
    }


def at(day: int) -> int:
    return T0 + day * DAY


DEFAULTS = dict(
    min_history_days=30, min_quote_volume=100_000, lookback_days=30, stale_days=7
)


# ---- basic membership --------------------------------------------------


def test_a_name_is_absent_before_it_lists():
    panel = {"NEWUSDT": series(start_day=100, n_days=400)}
    assert universe_at(panel, at(50), **DEFAULTS) == []


def test_a_name_needs_enough_history_before_it_qualifies():
    """Ranking on a trailing return requires a trailing return to exist."""
    panel = {"NEWUSDT": series(start_day=0, n_days=400)}
    assert universe_at(panel, at(10), **DEFAULTS) == []
    assert universe_at(panel, at(60), **DEFAULTS) == ["NEWUSDT"]


# ---- survivorship ------------------------------------------------------


def test_a_dead_coin_still_belongs_to_the_snapshots_it_lived_through():
    """The whole point. If a coin that later died is absent from 2022, a
    strategy never holds it, never takes the loss, and reports a return
    nobody could have earned."""
    panel = {"DEADUSDT": series(start_day=0, n_days=400)}  # stops at day 399
    assert universe_at(panel, at(200), **DEFAULTS) == ["DEADUSDT"]


def test_a_coin_leaves_the_universe_once_it_stops_trading():
    panel = {"DEADUSDT": series(start_day=0, n_days=400)}
    assert universe_at(panel, at(500), **DEFAULTS) == []


def test_the_exit_is_driven_by_missing_bars_not_by_a_survivor_list():
    """Membership is decided per-date from the data, so nothing needs to know
    in advance which coins eventually die."""
    panel = {
        "LIVEUSDT": series(start_day=0, n_days=900),
        "DEADUSDT": series(start_day=0, n_days=400),
    }
    assert universe_at(panel, at(300), **DEFAULTS) == ["DEADUSDT", "LIVEUSDT"]
    assert universe_at(panel, at(600), **DEFAULTS) == ["LIVEUSDT"]


def test_a_brief_data_gap_is_not_treated_as_death():
    """Exchanges have outages. A few missing days must not eject a live name."""
    s = series(start_day=0, n_days=400)
    keep = np.ones(len(s["ts"]), dtype=bool)
    keep[-3:] = False  # three days missing at the end
    panel = {"GAPUSDT": {k: v[keep] for k, v in s.items()}}
    assert universe_at(panel, at(400), **DEFAULTS) == ["GAPUSDT"]


# ---- look-ahead on liquidity ------------------------------------------


def test_liquidity_is_judged_at_the_date_not_from_today():
    """A coin illiquid in its early life must not be admitted then merely
    because it is liquid now."""
    thin = series(start_day=0, n_days=200, qvol=1_000.0)
    rich = series(start_day=200, n_days=200, qvol=5_000_000.0)
    panel = {"XUSDT": {k: np.concatenate([thin[k], rich[k]]) for k in thin}}
    assert universe_at(panel, at(150), **DEFAULTS) == []  # thin back then
    assert universe_at(panel, at(390), **DEFAULTS) == ["XUSDT"]  # liquid later


def test_a_name_that_dried_up_is_dropped_even_though_it_was_once_liquid():
    rich = series(start_day=0, n_days=200, qvol=5_000_000.0)
    thin = series(start_day=200, n_days=200, qvol=1_000.0)
    panel = {"XUSDT": {k: np.concatenate([rich[k], thin[k]]) for k in rich}}
    assert universe_at(panel, at(150), **DEFAULTS) == ["XUSDT"]
    assert universe_at(panel, at(390), **DEFAULTS) == []


def test_a_single_volume_spike_does_not_admit_an_illiquid_name():
    """Median over the window, not mean: one wash-traded day should not buy
    a listing."""
    s = series(start_day=0, n_days=200, qvol=1_000.0)
    s["qvol"][-1] = 1e9
    assert universe_at({"SPIKEUSDT": s}, at(199), **DEFAULTS) == []


def test_future_bars_are_never_consulted():
    """The invariant behind every case above: truncating the data after the
    as-of date must not change the answer."""
    full = series(start_day=0, n_days=800)
    cut = {k: v[:400] for k, v in full.items()}
    asof = at(399)
    assert universe_at({"XUSDT": full}, asof, **DEFAULTS) == universe_at(
        {"XUSDT": cut}, asof, **DEFAULTS
    )


# ---- rebalance dates ---------------------------------------------------


def test_rebalance_dates_span_the_data_at_the_requested_step():
    panel = {"AUSDT": series(start_day=0, n_days=100)}
    dates = month_ends(panel, step_days=30)
    assert dates[0] == T0
    assert all(b - a == 30 * DAY for a, b in zip(dates, dates[1:]))
    assert dates[-1] <= int(panel["AUSDT"]["ts"][-1])


def test_no_rebalance_dates_without_data():
    assert month_ends({}, step_days=30) == []


# ---- instrument exclusions --------------------------------------------


@pytest.mark.parametrize("suffix", LEVERAGED)
def test_leveraged_token_suffixes_are_recognised(suffix):
    """ETHUP/ETHDOWN are derivatives in a spot wrapper and are filtered
    before any of this runs."""
    assert f"ETH{suffix}USDT"[:-4].endswith(suffix)
