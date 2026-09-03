"""The suggested caps must stay consistent with the sizing rule they derive from.

`.env.example` carries starting values for the three exposure caps, derived from
`MAX_POS_PCT` and an assumed turnover. Those two numbers can move independently:
someone halves the position size and the caps silently become eight times looser
than intended, or raises it and the caps start binding in normal operation and
trip the kill switch.

Neither failure announces itself. A cap that is too loose does nothing visibly,
and a cap that is too tight looks like a broken engine rather than a
misconfiguration. So the relationship is pinned here rather than left in a
comment -- the same reason `test_live_gate_message.py` exists.

These check the *documented example*, not a live deployment. Real caps are set
in `.env` against real equity; what is asserted is that the guidance shipped in
the template is internally coherent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: The nominal equity the worked example in `.env.example` is derived against.
NOMINAL_EQUITY = 10_000.0

#: The turnover the example assumes, pending measurement in the testnet soak.
ROUND_TRIPS_PER_DAY = 20
ROUND_TRIPS_PER_SYMBOL_PER_DAY = 4
LEGS = 2


def _env_example() -> dict:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


@pytest.fixture(scope="module")
def env() -> dict:
    return _env_example()


def _f(env, key) -> float:
    assert key in env, f"{key} missing from .env.example"
    return float(env[key])


# ---- the caps are actually enabled ---------------------------------------


@pytest.mark.parametrize(
    "key", ["MAX_ORDER_NOTIONAL", "MAX_SYMBOL_NOTIONAL_24H", "MAX_DAILY_NOTIONAL"]
)
def test_the_example_no_longer_ships_them_disabled(env, key):
    """They defaulted to 0. RUNBOOK section 8 lists setting them as a
    prerequisite, and shipping a template that disables them made the
    prerequisite easy to miss."""
    assert _f(env, key) > 0, f"{key} is 0, which disables the cap"


# ---- consistency with the sizing rule ------------------------------------


def test_the_per_order_cap_is_a_backstop_not_the_sizing_rule(env):
    """It should sit above the intended position size -- its job is catching a
    sizing bug, not enforcing position sizing, which MAX_POS_PCT already does.
    Equal to the sizing rule and rounding alone would block ordinary orders."""
    expected = NOMINAL_EQUITY * _f(env, "MAX_POS_PCT")
    cap = _f(env, "MAX_ORDER_NOTIONAL")
    assert cap > expected, "per-order cap would bind on a normally-sized order"
    assert cap <= 4 * expected, "per-order cap so loose it would not catch a bug"


def test_the_daily_cap_exceeds_expected_turnover(env):
    """The trap this guards. A cap set from position size rather than turnover
    halts the engine within the hour."""
    expected = NOMINAL_EQUITY * _f(env, "MAX_POS_PCT") * ROUND_TRIPS_PER_DAY * LEGS
    assert _f(env, "MAX_DAILY_NOTIONAL") > expected, (
        f"daily cap is below expected turnover of {expected:,.0f}; the engine "
        "would block normal trading"
    )


def test_the_daily_cap_still_bounds_a_runaway(env):
    """Headroom, not an open door. Beyond roughly twice expected traffic it
    stops being a backstop."""
    expected = NOMINAL_EQUITY * _f(env, "MAX_POS_PCT") * ROUND_TRIPS_PER_DAY * LEGS
    assert _f(env, "MAX_DAILY_NOTIONAL") <= 2 * expected


def test_the_symbol_cap_exceeds_expected_per_symbol_turnover(env):
    expected = (
        NOMINAL_EQUITY * _f(env, "MAX_POS_PCT") * ROUND_TRIPS_PER_SYMBOL_PER_DAY * LEGS
    )
    assert _f(env, "MAX_SYMBOL_NOTIONAL_24H") > expected


# ---- the caps relate to each other sensibly ------------------------------


def test_a_single_symbol_cannot_consume_the_whole_day(env):
    """If the per-symbol cap reached the daily cap it would be redundant, and
    one runaway symbol could exhaust the day's budget alone."""
    assert _f(env, "MAX_SYMBOL_NOTIONAL_24H") < _f(env, "MAX_DAILY_NOTIONAL")


def test_one_order_cannot_exhaust_a_symbols_day(env):
    assert _f(env, "MAX_ORDER_NOTIONAL") < _f(env, "MAX_SYMBOL_NOTIONAL_24H")


# ---- the code default stays off ------------------------------------------


def test_the_code_default_remains_disabled():
    """The template suggests values; the code must not assume them. A wrong
    absolute figure baked into a default is worse than an explicit one, since
    it would apply to accounts of every size."""
    from intradyne.core.config import Settings

    s = Settings()
    assert s.guardrails.max_order_notional == 0.0
    assert s.guardrails.max_daily_notional == 0.0
