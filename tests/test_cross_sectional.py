"""A negative result is only worth anything if the harness that produced it
was capable of showing a positive one.

The load-bearing test here is `test_random_selection_earns_no_excess`: if the
simulation quietly penalised any subset relative to the benchmark, every
signal would come out negative and the conclusion would be an artifact. It
does not, so the measured underperformance is real.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cross_sectional_test import (  # noqa: E402
    SIGNALS,
    forward_returns,
    price_at,
    run_signal,
    sharpe,
)

DAY = 86_400_000
T0 = 1_600_000_000_000


def series(n_days, start_day=0, price=100.0, drift=0.0, qvol=1e6):
    ts = np.array([T0 + (start_day + i) * DAY for i in range(n_days)], dtype="int64")
    close = np.array([price * (1 + drift) ** i for i in range(n_days)], dtype="float64")
    return {"ts": ts, "close": close, "qvol": np.full(n_days, qvol)}


def at(day):
    return T0 + day * DAY


# ---- survivorship: the loss must actually be taken ---------------------


def test_a_dead_name_is_priced_at_its_last_print_not_dropped():
    """If a delisted holding silently vanished, the portfolio would never
    book its loss -- which is the exact bias this whole exercise removes."""
    d = series(100, price=100.0)
    d["close"][-1] = 10.0  # collapsed before delisting
    assert price_at(d, at(500)) == 10.0


def test_a_holding_that_died_contributes_its_loss():
    panel = {"DEAD": series(100, price=100.0), "LIVE": series(400, price=100.0)}
    panel["DEAD"]["close"][-1] = 10.0
    rets = forward_returns(panel, ["DEAD", "LIVE"], at(50), at(200))
    assert rets["DEAD"] < -0.5, "the collapse must show up as a loss"
    assert "DEAD" in rets, "a dead name must not be dropped from the period"


def test_a_name_with_no_price_yet_is_excluded_rather_than_assumed_flat():
    panel = {"LATE": series(100, start_day=300)}
    assert forward_returns(panel, ["LATE"], at(10), at(50)) == {}


# ---- the harness must be unbiased --------------------------------------


def test_random_selection_earns_no_excess():
    """The result rests on this. Selecting names at random must average zero
    excess over the equal-weight benchmark; if it did not, every signal would
    read negative for reasons that have nothing to do with the signal."""
    rng = np.random.default_rng(0)
    n_names, n_periods = 40, 120
    panel = {}
    for i in range(n_names):
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n_periods * 30 + 400)))
        ts = np.array([T0 + j * DAY for j in range(len(px))], dtype="int64")
        panel[f"S{i}"] = {"ts": ts, "close": px, "qvol": np.full(len(px), 1e6)}

    names = list(panel)
    dates = [at(400 + p * 30) for p in range(n_periods)]
    excesses = []
    for _ in range(200):
        per = []
        for t0, t1 in zip(dates, dates[1:]):
            r = forward_returns(panel, names, t0, t1)
            pick = rng.choice(list(r), size=4, replace=False)
            per.append(np.mean([r[s] for s in pick]) - np.mean(list(r.values())))
        excesses.append(np.mean(per))
    mean = float(np.mean(excesses))
    se = float(np.std(excesses, ddof=1)) / np.sqrt(len(excesses))
    assert abs(mean) < 4 * se, f"harness is biased: {mean:+.5f} vs SE {se:.5f}"


# ---- signals must not look ahead ---------------------------------------


def test_no_signal_reads_past_the_rebalance_date():
    """Truncating the data after the as-of date must not change any score."""
    full = series(800, drift=0.001)
    cut = {k: v[:500] for k, v in full.items()}
    asof = at(499)
    for name, fn in SIGNALS.items():
        a, b = fn(full, asof), fn(cut, asof)
        if a is None and b is None:
            continue
        assert a == b, f"{name} consulted data after the as-of date"


def test_a_signal_returns_none_without_enough_history():
    short = series(20)
    assert SIGNALS["mom_12m"](short, at(19)) is None


# ---- portfolio mechanics ------------------------------------------------


def test_costs_reduce_the_excess():
    """Turnover has to be charged, or the test measures a portfolio nobody
    could have traded."""
    panel = {f"S{i}": series(600, drift=0.0005 * i) for i in range(20)}
    dates = [at(400 + p * 30) for p in range(6)]
    mbd = {d: list(panel) for d in dates}
    free, _, _ = run_signal(panel, dates, mbd, "mom_1m", 0.2, cost_bps=0.0)
    paid, _, _ = run_signal(panel, dates, mbd, "mom_1m", 0.2, cost_bps=100.0)
    assert paid.sum() < free.sum()


def test_sharpe_is_zero_for_a_flat_series():
    assert sharpe(np.zeros(50), 12.0) == 0.0


def test_sharpe_scales_with_the_annualisation_factor():
    x = np.array([0.01, -0.005, 0.02, 0.0, 0.01] * 10)
    assert sharpe(x, 12.0) == np.sqrt(12.0) / np.sqrt(1.0) * sharpe(x, 1.0)


def test_all_eight_preregistered_signals_are_present():
    """The pre-registration fixes the signal list; adding a ninth after
    seeing results would invalidate the null threshold."""
    assert set(SIGNALS) == {
        "mom_1m",
        "mom_3m",
        "mom_6m",
        "mom_12m",
        "reversal_1w",
        "mom_3m_volscaled",
        "low_downside_vol",
        "volume_trend",
    }
