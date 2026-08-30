"""A measured result that lives only in a markdown file is one `git pull`
away from being forgotten. These pin it into the boot path."""

import pytest

from intradyne.core.config import (
    ACKNOWLEDGE_NO_EDGE,
    STRATEGY_EDGE_DEMONSTRATED,
    assert_strategy_edge_gate,
    load_settings,
)


def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    load_settings.cache_clear()
    return load_settings()


def test_the_gate_is_shut_by_default():
    """If this ever reads True without a measurement behind it, the whole
    point of the gate is gone."""
    assert STRATEGY_EDGE_DEMONSTRATED is False


def test_the_loop_refuses_to_start_on_a_strategy_with_no_edge(monkeypatch):
    s = _settings(monkeypatch, ENGINE_ENABLED="true")
    with pytest.raises(RuntimeError) as exc:
        assert_strategy_edge_gate(s)
    msg = str(exc.value)
    # The error has to carry the number and the way out, or the next person
    # hits it blind and just deletes the check.
    assert "0.5bps" in msg
    assert ACKNOWLEDGE_NO_EDGE in msg


def test_running_the_api_without_the_loop_is_unaffected(monkeypatch):
    """The gate is about trading, not about serving the API."""
    assert_strategy_edge_gate(_settings(monkeypatch, ENGINE_ENABLED="false"))


def test_research_can_proceed_when_acknowledged(monkeypatch):
    """Paper trading is how a replacement strategy gets validated, so an
    outright refusal would block the only legitimate path forward."""
    s = _settings(monkeypatch, ENGINE_ENABLED="true", ACKNOWLEDGE_NO_EDGE="true")
    assert_strategy_edge_gate(s)


def test_the_acknowledgement_must_be_explicit(monkeypatch):
    """A stray or empty value must not read as consent."""
    s = _settings(monkeypatch, ENGINE_ENABLED="true", ACKNOWLEDGE_NO_EDGE="")
    with pytest.raises(RuntimeError):
        assert_strategy_edge_gate(s)


def test_the_gate_does_not_open_live_trading(monkeypatch):
    """Acknowledging the lack of edge is not authorisation to trade real
    money; the live gate is a separate, non-overridable check."""
    from intradyne.core.config import assert_live_trading_gate

    s = _settings(
        monkeypatch,
        ENGINE_ENABLED="true",
        ACKNOWLEDGE_NO_EDGE="true",
        MODE="live",
        LIVE_TRADING_ENABLED="true",
    )
    assert_strategy_edge_gate(s)  # acknowledged, so this passes
    with pytest.raises(RuntimeError):
        assert_live_trading_gate(s)  # but this still does not


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    load_settings.cache_clear()
