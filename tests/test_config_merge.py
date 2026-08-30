"""The API and the engine used to have separate Settings classes reading
overlapping but differently-named environment variables. These tests pin the
merged behaviour.
"""

from __future__ import annotations

import pytest

from intradyne.core.config import load_settings, reset_settings_cache


SHARED = ("FLASH_CRASH_PCT", "FLASH_CRASH_DROP_1H", "KILL_SWITCH_BREACHES")
CREDS = ("BITGET_API_KEY", "API_KEY", "BITGET_API_SECRET", "API_SECRET")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    for k in SHARED + CREDS + ("APP_ENV", "SENTIMENT_ENABLE", "SENTIMENT_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    return monkeypatch


def test_flash_crash_defaults_agree_across_tiers(env):
    s = load_settings()
    assert s.guardrails.flash_crash_pct == s.risk.flash_crash_drop_1h


@pytest.mark.parametrize("name", ["FLASH_CRASH_PCT", "FLASH_CRASH_DROP_1H"])
def test_either_flash_crash_name_arms_both_tiers(env, name):
    """Previously FLASH_CRASH_PCT armed only the API guardrail and
    FLASH_CRASH_DROP_1H only the engine, leaving the other at its default."""
    env.setenv(name, "0.07")
    reset_settings_cache()
    s = load_settings()
    assert s.guardrails.flash_crash_pct == 0.07
    assert s.risk.flash_crash_drop_1h == 0.07


def test_kill_switch_shared_across_tiers(env):
    env.setenv("KILL_SWITCH_BREACHES", "9")
    reset_settings_cache()
    s = load_settings()
    assert s.guardrails.kill_switch_breaches == 9
    assert s.risk.kill_switch_breaches == 9


def test_legacy_api_key_alias_still_works(env):
    """The engine read API_KEY for the broker credential."""
    env.setenv("API_KEY", "legacy")
    reset_settings_cache()
    assert load_settings().bitget_api_key == "legacy"


def test_canonical_name_wins_over_legacy_alias(env):
    env.setenv("API_KEY", "legacy")
    env.setenv("BITGET_API_KEY", "canonical")
    reset_settings_cache()
    assert load_settings().bitget_api_key == "canonical"


def test_broker_credential_is_not_the_http_api_key(env):
    """API_KEY is the broker credential. The HTTP key is X_API_KEY, and the
    two must never be conflated -- see tests/test_api_auth.py."""
    from intradyne.api.deps import configured_api_key

    env.setenv("API_KEY", "broker-secret")
    env.delenv("X_API_KEY", raising=False)
    reset_settings_cache()
    assert load_settings().bitget_api_key == "broker-secret"
    assert configured_api_key() == ""


@pytest.mark.parametrize("name", ["SENTIMENT_ENABLED", "SENTIMENT_ENABLE"])
def test_either_sentiment_flag_name_works(env, name):
    env.setenv(name, "true")
    reset_settings_cache()
    assert load_settings().sentiment_enabled is True


def test_drawdown_tiers_stay_distinct(env):
    """Session drawdown (engine) and 30-day drawdown (guardrails) are
    different measurements and must not be collapsed into one setting."""
    env.setenv("DD_SOFT", "0.03")
    env.setenv("DD_WARN_PCT", "0.15")
    reset_settings_cache()
    s = load_settings()
    assert s.risk.dd_soft == 0.03
    assert s.guardrails.dd_warn_pct == 0.15
