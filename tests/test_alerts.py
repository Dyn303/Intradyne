"""Alerts sit next to the order path, so the tests are mostly about what they
must *not* do.

An alerting bug that drops a notification costs an unread message. An alerting
bug that raises inside `set_halt` leaves the halt half-applied, and an alerting
bug that formats a token into a message publishes a credential. Those are the
two failure modes worth pinning.
"""

import threading

import pytest

from intradyne.core.alerts import (
    CRITICAL_EVENTS,
    AlertConfig,
    Alerter,
    alert,
    reset_alerter,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_alerter()
    yield
    reset_alerter()


def _enabled(**kw) -> Alerter:
    return Alerter(cfg=AlertConfig(token="TESTTOKEN", chat_id="42", **kw))


# ---- it must never break the caller ------------------------------------


def test_a_network_failure_never_propagates(monkeypatch):
    """The halt path calls this. An exception here would leave a halt
    half-applied, which is worse than a missed notification."""

    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("intradyne.core.alerts.httpx.post", boom)
    assert _enabled().send("halt_engaged", {"reason": "x"}) is False


def test_an_api_rejection_never_propagates(monkeypatch):
    class Resp:
        status_code = 401

    monkeypatch.setattr("intradyne.core.alerts.httpx.post", lambda *a, **k: Resp())
    assert _enabled().send("halt_engaged") is False


def test_halting_still_works_when_alerting_explodes(monkeypatch):
    """The integration that matters: set_halt must complete regardless."""
    from intradyne.risk import kill_switch

    monkeypatch.setattr(
        "intradyne.risk.kill_switch.alert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    try:
        with pytest.raises(RuntimeError):
            kill_switch.set_halt(True, "reason")
        # Even though the alert raised, the halt itself was applied first.
        assert kill_switch.is_halted() is True
    finally:
        monkeypatch.undo()
        kill_switch.set_halt(False)


def test_the_module_level_helper_swallows_everything(monkeypatch):
    monkeypatch.setattr(
        "intradyne.core.alerts.get_alerter",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert alert("halt_engaged") is False


# ---- secrets must not reach the message --------------------------------


def test_secret_looking_payload_fields_are_redacted():
    body = Alerter.format("halt_engaged", {"api_key": "bg_livesecret", "dd": 0.21})
    assert "bg_livesecret" not in body
    assert "0.21" in body


def test_the_bot_token_never_appears_in_a_message():
    a = _enabled()
    body = a.format("halt_engaged", {"reason": "drawdown"})
    assert "TESTTOKEN" not in body


def test_long_values_are_truncated():
    body = Alerter.format("halt_engaged", {"reason": "x" * 5000})
    assert len(body) < 400


# ---- disabled by default ------------------------------------------------


def test_no_token_means_no_send_and_no_error(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    reset_alerter()
    called = []
    monkeypatch.setattr(
        "intradyne.core.alerts.httpx.post", lambda *a, **k: called.append(1)
    )
    assert alert("halt_engaged") is False
    assert called == [], "a disabled alerter must not touch the network"


def test_a_partial_configuration_stays_disabled():
    assert AlertConfig(token="t", chat_id=None).enabled is False
    assert AlertConfig(token=None, chat_id="1").enabled is False


# ---- rate limiting -------------------------------------------------------


def test_repeated_alerts_are_suppressed_within_the_cooldown(monkeypatch):
    """A guardrail tripping on every tick would otherwise send hundreds of
    messages a minute and get the bot rate-limited into uselessness."""

    class Resp:
        status_code = 200

    sent = []
    monkeypatch.setattr(
        "intradyne.core.alerts.httpx.post", lambda *a, **k: (sent.append(1), Resp())[1]
    )
    a = _enabled(cooldown_s=600.0)
    assert a.send("halt_engaged") is True
    assert a.send("halt_engaged") is False
    assert len(sent) == 1


def test_different_events_are_not_suppressed_by_each_other(monkeypatch):
    class Resp:
        status_code = 200

    monkeypatch.setattr("intradyne.core.alerts.httpx.post", lambda *a, **k: Resp())
    a = _enabled(cooldown_s=600.0)
    assert a.send("halt_engaged") is True
    assert a.send("kill_switch_fired") is True


def test_the_cooldown_is_thread_safe(monkeypatch):
    """Guardrails run on the engine thread while the API can halt from
    another; exactly one message should escape."""

    class Resp:
        status_code = 200

    sent = []
    monkeypatch.setattr(
        "intradyne.core.alerts.httpx.post", lambda *a, **k: (sent.append(1), Resp())[1]
    )
    a = _enabled(cooldown_s=600.0)
    threads = [
        threading.Thread(target=a.send, args=("halt_engaged",)) for _ in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sent) == 1


# ---- only the events worth waking someone for ---------------------------


def test_routine_events_are_not_sent(monkeypatch):
    called = []
    monkeypatch.setattr(
        "intradyne.core.alerts.httpx.post", lambda *a, **k: called.append(1)
    )
    assert _enabled().send("order_filled") is False
    assert called == []


def test_force_overrides_the_critical_list(monkeypatch):
    class Resp:
        status_code = 200

    monkeypatch.setattr("intradyne.core.alerts.httpx.post", lambda *a, **k: Resp())
    assert _enabled().send("anything", force=True) is True


def test_the_critical_list_covers_the_unattended_failures():
    """These are the events that happen when nobody is watching, which is the
    entire reason outbound alerting exists."""
    assert {"halt_engaged", "kill_switch_fired", "engine_crashed"} <= CRITICAL_EVENTS
