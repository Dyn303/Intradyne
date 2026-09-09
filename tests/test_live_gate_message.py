"""The live gate must not lie about why it is shut.

Its message named four controls as missing -- idempotency, reconciliation,
notional caps and halt alerting -- long after all four were built, wired and
covered by `tests/test_live_readiness.py`. RUNBOOK section 8 had been updated;
the message had not, and it is the message an operator sees at exactly the
moment they try to go live.

That is worse than an out-of-date comment. A reader either distrusts a system
further along than it claims, or goes and implements a second copy of working
machinery. This project has already fixed two endpoints that reported success
without acting, on the grounds that a control which misinforms is worse than a
missing one; the same standard applies to a control that misinforms about
itself.

Prose cannot be tested, so these tie each claim to the code. A control named as
built must be importable and wired; a blocker named as remaining must actually
be unmet. If someone builds the remaining work and forgets the message, or
edits the message without the work, one of these fails.
"""

from __future__ import annotations

import pytest

from intradyne.core import config as C


@pytest.fixture()
def message() -> str:
    """The text an operator actually sees when arming live trading."""
    s = C.load_settings()
    try:
        s.mode = "live"
        s.live_trading_enabled = True
    except Exception:  # noqa: BLE001 - pydantic models may be frozen
        pytest.skip("settings not mutable in this build")
    with pytest.raises(RuntimeError) as exc:
        C.assert_live_trading_gate(s)
    return str(exc.value)


# ---- the gate still refuses -----------------------------------------------


def test_the_gate_is_still_shut():
    """Correcting the reason must not open the door. There is still no
    demonstrated edge, and the flag is the thing that says so."""
    assert C.LIVE_TRADING_GATE_OPEN is False


def test_the_gate_raises_when_live_is_armed(message):
    assert "Live trading is armed" in message


# ---- controls named as built are actually built ---------------------------


def test_idempotency_exists_and_is_used_in_the_order_path():
    from intradyne.core.idempotency import OrderKeyStore, make_key  # noqa: F401
    from intradyne.engine import execution

    src = execution.__file__
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "OrderKeyStore" in body, "claimed as built, not referenced in execution"


def test_reconciliation_exists_and_runs_at_startup():
    from intradyne.engine.reconcile import reconcile_on_start  # noqa: F401
    from intradyne.engine import loop

    with open(loop.__file__, encoding="utf-8") as fh:
        body = fh.read()
    assert "reconcile_on_start(" in body, "imported but never called"


def test_notional_caps_exist_and_are_enforced():
    from intradyne.core.limits import NotionalTracker  # noqa: F401
    from intradyne.risk import guardrails

    with open(guardrails.__file__, encoding="utf-8") as fh:
        body = fh.read()
    assert "_check_exposure" in body


def test_halt_alerting_exists_and_fires():
    from intradyne.core.alerts import alert  # noqa: F401
    from intradyne.risk import kill_switch

    with open(kill_switch.__file__, encoding="utf-8") as fh:
        body = fh.read()
    assert "alert(" in body, "alerting claimed, but the halt path does not fire it"


# ---- the message says what is true ---------------------------------------


@pytest.mark.parametrize(
    "control", ["idempotency", "reconciliation", "notional caps", "alerting"]
)
def test_no_built_control_is_described_as_missing(message, control):
    """The specific regression. Each of these is built; none may appear in a
    sentence claiming it is absent."""
    lowered = message.lower()
    idx = lowered.find(control.split()[0])
    assert idx >= 0, f"{control} should be named among what is built"
    window = lowered[max(0, idx - 120) : idx + 120]
    for word in ("still missing", "not done", "is missing", "yet to be"):
        assert word not in window, f"{control} described as missing: {window!r}"


def test_the_message_names_the_real_remaining_blocker(message):
    """The edge, not the controls. That is what actually keeps this shut."""
    assert "STRATEGY_EDGE_DEMONSTRATED" in message
    assert C.STRATEGY_EDGE_DEMONSTRATED is False


def test_the_message_warns_that_caps_default_to_disabled(message):
    """Built is not the same as configured. The caps default to 0, and arming
    live without setting them leaves volume bounded only by risk thresholds."""
    assert "default to 0" in message


def test_the_message_points_somewhere_current(message):
    """RUNBOOK section 8 is maintained; the old message pointed at a MIGRATION
    phase that had since been completed."""
    assert "RUNBOOK" in message


def test_the_message_still_tells_the_operator_what_to_do(message):
    assert "MODE=paper" in message
