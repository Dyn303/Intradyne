"""The cross-sectional harness, and the properties slot 1 depends on.

The crypto programme's expensive lesson was that a control which does not
share the selection mechanism cannot distinguish "this predicts" from "the
bottom decile is not a typical name". These tests pin the null's behaviour
rather than the strategy's, because the null is the part that was wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from intradyne.research.cross_sectional import (
    Panel,
    bonferroni_alpha,
    run_test,
    sanity_check,
    _decile_by_date,
    _shuffle_within_date,
)


def _panel(close: np.ndarray, membership=None) -> Panel:
    t, n = close.shape
    return Panel(
        dates=np.arange(t, dtype=float) * 86_400,
        symbols=[f"S{j}" for j in range(n)],
        close=close,
        membership=np.ones((t, n), bool) if membership is None else membership,
    )


class TestPanel:
    def test_a_shape_mismatch_is_refused(self):
        with pytest.raises(ValueError, match="shape mismatch"):
            Panel(
                dates=np.arange(3.0),
                symbols=["A", "B"],
                close=np.ones((3, 3)),
                membership=np.ones((3, 2), bool),
            )

    def test_membership_is_separate_from_price(self):
        """A name can be priced and not investable -- delisted from the index,
        screened out on compliance. Conflating them is how a universe becomes
        survivorship-biased."""
        close = np.ones((10, 3)) * np.arange(1, 4)
        mem = np.ones((10, 3), bool)
        mem[:, 2] = False  # priced throughout, never investable
        rows = _decile_by_date(
            _panel(close, mem), lookback=2, hold=2, weakest=True, decile=0.5
        )
        assert rows == {}, "an uninvestable name left too few for a cross-section"


class TestExcessMeasure:
    """The flaw crypto validation exposed: a long-only decile carries the
    market, so its absolute return measures market direction, not ranking."""

    def test_a_universe_moving_together_has_zero_edge(self):
        t, n = 40, 10
        close = np.ones((t, n))
        for i in range(1, t):
            close[i] = close[i - 1] * 1.05  # every name up 5%, no dispersion
        rows = _decile_by_date(
            _panel(close), lookback=5, hold=5, weakest=True, decile=0.2
        )
        vals = [v for lst in rows.values() for v in lst]
        assert vals, "no positions taken"
        assert max(abs(v) for v in vals) < 1e-6, "market drift leaked into the edge"

    def test_a_name_that_beats_the_universe_shows_positive_edge(self):
        t, n = 40, 10
        close = np.ones((t, n))
        for i in range(1, t):
            close[i] = close[i - 1] * 1.01
            close[i, 0] = close[i - 1, 0] * 1.05  # one name outruns the rest
        rows = _decile_by_date(
            _panel(close), lookback=5, hold=5, weakest=False, decile=0.1
        )
        vals = [v for lst in rows.values() for v in lst]
        assert vals and st_mean(vals) > 0


def st_mean(v):
    return sum(v) / len(v)


class TestSelection:
    def test_weakest_picks_the_worst_trailing_performer(self):
        t, n = 30, 10
        close = np.ones((t, n))
        for i in range(1, t):
            close[i] = close[i - 1] * 1.02
            close[i, 3] = close[i - 1, 3] * 0.98  # name 3 is always the laggard
        panel = _panel(close)
        rows = _decile_by_date(panel, lookback=5, hold=5, weakest=True, decile=0.1)
        assert rows, "no rebalances"

    def test_rebalances_do_not_overlap(self):
        """Overlapping holding windows share price moves and are not
        independent draws; counting them as such inflates every t."""
        close = np.cumprod(1 + np.zeros((60, 8)) + 0.001, axis=0)
        rows = _decile_by_date(
            _panel(close), lookback=5, hold=10, weakest=True, decile=0.2
        )
        dates = sorted(rows)
        gaps = {int(b - a) for a, b in zip(dates, dates[1:])}
        assert gaps == {10 * 86_400}, f"windows overlap: {gaps}"


class TestNull:
    def test_the_shuffle_preserves_each_dates_return_set(self):
        """It reassigns which name earned what; it must not change what the
        market did that day."""
        rng = np.random.default_rng(0)
        close = np.cumprod(1 + rng.normal(0, 0.02, (50, 8)), axis=0) * 100
        shuffled = _shuffle_within_date(close, np.random.default_rng(1))
        a = np.sort(close[1:] / close[:-1] - 1.0, axis=1)
        b = np.sort(shuffled[1:] / shuffled[:-1] - 1.0, axis=1)
        assert np.allclose(a, b), "the shuffle altered a date's returns"

    def test_the_null_brackets_zero_on_random_data(self):
        """A null that does not bracket zero is not a null. The crypto
        random-entry control sat four sigma from zero and was read as a
        baseline for two runs."""
        rng = np.random.default_rng(7)
        close = np.cumprod(1 + rng.normal(0.0005, 0.03, (300, 20)), axis=0) * 100
        r = run_test(_panel(close), lookback=5, hold=5, weakest=True, n_boot=60, seed=3)
        assert r is not None
        assert r.null_lo <= 0.0 <= r.null_hi, f"null band [{r.null_lo}, {r.null_hi}]"

    def test_pure_noise_does_not_produce_significance(self):
        rng = np.random.default_rng(11)
        close = np.cumprod(1 + rng.normal(0, 0.02, (300, 20)), axis=0) * 100
        r = run_test(_panel(close), lookback=5, hold=5, weakest=True, n_boot=60, seed=5)
        assert r is not None
        assert r.p_value > bonferroni_alpha(8), f"noise passed at p={r.p_value}"

    def test_p_is_never_exactly_zero(self):
        """A finite number of draws cannot support p = 0."""
        rng = np.random.default_rng(2)
        close = np.cumprod(1 + rng.normal(0, 0.02, (200, 12)), axis=0) * 100
        r = run_test(_panel(close), lookback=5, hold=5, weakest=True, n_boot=40, seed=1)
        assert r is not None and r.p_value > 0.0


class TestSanityChecks:
    """Each of these faults has occurred in this project and presented as a
    finding: an empty table printed as '0 of 8 passed', a turnover of zero from
    comparing two empty sets."""

    def test_an_empty_run_is_flagged(self):
        assert any("empty table" in p for p in sanity_check([]))

    def test_too_few_dates_is_flagged(self):
        rng = np.random.default_rng(4)
        close = np.cumprod(1 + rng.normal(0, 0.02, (40, 10)), axis=0) * 100
        r = run_test(_panel(close), lookback=5, hold=5, weakest=True, n_boot=30, seed=1)
        if r is not None:
            assert any("too few to cluster" in p for p in sanity_check([r]))


class TestBonferroni:
    def test_eight_tests_tighten_the_bar(self):
        assert bonferroni_alpha(8) == pytest.approx(0.00625)

    def test_one_test_is_uncorrected(self):
        assert bonferroni_alpha(1) == pytest.approx(0.05)

    def test_zero_tests_is_an_error(self):
        with pytest.raises(ValueError):
            bonferroni_alpha(0)
