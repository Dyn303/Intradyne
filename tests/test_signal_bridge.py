"""A research signal and its engine form must be the same thing.

Two implementations of one rule drift, and the drift is invisible: the research
number stays right, the traded rule quietly becomes something else, and nothing
raises. `VectorSignal` removes the second implementation by calling the
research function itself -- so what these tests check is that the streaming
wrapper is faithful, and that the harness can tell when it is not.

The one that matters most is `test_a_look_ahead_signal_is_caught`. A streaming
run has only the bars up to the current index, so a signal that disagrees at
every buffer length is reading the future. That makes this a look-ahead
detector, which the framework treats as a rejection condition and which nothing
in this repo could previously test mechanically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from signal_bridge import (  # noqa: E402
    FIELDS,
    VectorSignal,
    agreement,
    minimum_buffer,
    replay,
    slice_bars,
    synthetic_bars,
)
from strategy_search import build_strategies  # noqa: E402

BARS = synthetic_bars(400, seed=3)
CANDS = (40, 80, 160)


def rolling_high_break(n):
    """Causal: fires when close is the highest of the last n bars."""

    def f(b):
        out = np.zeros(len(b), dtype=bool)
        for i in range(n, len(b)):
            out[i] = b.close[i] >= b.close[i - n : i + 1].max()
        return out

    return f


def peeks_ahead(k=3):
    """Deliberately broken: fires when the close k bars *later* is higher."""

    def f(b):
        out = np.zeros(len(b), dtype=bool)
        if len(b) > k:
            out[:-k] = b.close[k:] > b.close[:-k]
        return out

    return f


# ---- the look-ahead detector ---------------------------------------------


def test_a_look_ahead_signal_is_caught():
    """The reason this harness is worth having. A signal reading future bars
    can never be reproduced from a streaming run, at any buffer length."""
    assert minimum_buffer(peeks_ahead(3), BARS, CANDS) is None


def test_a_look_ahead_signal_disagrees_substantially():
    """Not a rounding difference -- a large fraction of bars differ, which is
    what separates look-ahead from a warm-up artefact."""
    a = agreement(peeks_ahead(3), BARS, 160, warmup=160)
    assert a["disagreements"] > 0.2 * a["compared"]


def test_a_causal_signal_of_the_same_shape_is_not_flagged():
    """The detector must not simply reject everything."""
    assert minimum_buffer(rolling_high_break(20), BARS, CANDS) is not None


# ---- faithfulness of the streaming wrapper -------------------------------


def test_a_finite_lookback_reproduces_exactly_once_the_buffer_covers_it():
    assert minimum_buffer(rolling_high_break(20), BARS, (40, 80, 160)) == 40


def test_too_short_a_buffer_disagrees():
    a = agreement(rolling_high_break(60), BARS, 20, warmup=160)
    assert not a["exact"]


@pytest.mark.parametrize(
    "name", ["breakout_60s", "mom_60s_k1", "revert_60s_k1.5", "ema_5x30"]
)
def test_real_research_signals_round_trip(name):
    """Signals from the library the searches actually used."""
    fn = build_strategies()[name]
    assert minimum_buffer(fn, synthetic_bars(700, seed=11), (60, 120, 240)) is not None


# ---- the streaming path is genuinely causal ------------------------------


def test_changing_a_future_bar_cannot_change_an_earlier_signal():
    """The strongest statement of causality available without inspecting the
    function: perturb the tail, and every entry before it must be identical."""
    fn = build_strategies()["breakout_60s"]
    a = replay(fn, BARS, 120)

    tampered = slice_bars(BARS, 0, len(BARS))
    tampered.close[-40:] = tampered.close[-40:] * 1.05
    tampered.high[-40:] = tampered.high[-40:] * 1.05
    b = replay(fn, tampered, 120)

    cut = len(BARS) - 40
    assert np.array_equal(a[:cut], b[:cut])


def test_the_comparison_window_does_not_shrink_with_the_buffer():
    """A regression on the harness itself. `warmup` once followed the buffer,
    so a longer buffer was judged on fewer bars and could score exact by being
    examined less rather than by being right."""
    fn = rolling_high_break(20)
    small = agreement(fn, BARS, 40, warmup=160)
    large = agreement(fn, BARS, 160, warmup=160)
    assert small["compared"] == large["compared"]


# ---- the adapter satisfies the router's contract -------------------------


def test_on_bar_returns_the_routers_shape():
    sig = VectorSignal(fn=rolling_high_break(5), buffer_bars=40, symbol="AAPL")
    fired = None
    for i in range(len(BARS)):
        got = sig.on_bar({f: float(getattr(BARS, f)[i]) for f in FIELDS})
        if got is not None:
            fired = got
            break
    assert fired is not None
    assert fired["action"] == "buy"
    assert isinstance(fired["reason"], str)
    assert isinstance(fired["features"], dict)


def test_no_signal_before_two_bars():
    sig = VectorSignal(fn=rolling_high_break(5), buffer_bars=40)
    first = sig.on_bar({f: float(getattr(BARS, f)[0]) for f in FIELDS})
    assert first is None


def test_a_signal_that_raises_on_a_short_window_is_warm_up_not_an_error():
    """A window shorter than the lookback means "not yet", not "broken"."""

    def brittle(b):
        if len(b) < 50:
            raise ValueError("too short")
        return np.zeros(len(b), dtype=bool)

    sig = VectorSignal(fn=brittle, buffer_bars=80)
    for i in range(10):
        assert sig.on_bar({f: float(getattr(BARS, f)[i]) for f in FIELDS}) is None


def test_the_buffer_is_bounded():
    sig = VectorSignal(fn=rolling_high_break(5), buffer_bars=50)
    for i in range(len(BARS)):
        sig.on_bar({f: float(getattr(BARS, f)[i]) for f in FIELDS})
    assert len(sig) == 50


# ---- slicing --------------------------------------------------------------


def test_slice_bars_keeps_every_field_aligned():
    s = slice_bars(BARS, 10, 20)
    assert len(s) == 10
    for f in FIELDS:
        assert np.array_equal(getattr(s, f), getattr(BARS, f)[10:20])


def test_synthetic_bars_are_deterministic():
    assert np.array_equal(
        synthetic_bars(50, seed=1).close, synthetic_bars(50, seed=1).close
    )
    assert not np.array_equal(
        synthetic_bars(50, seed=1).close, synthetic_bars(50, seed=2).close
    )
