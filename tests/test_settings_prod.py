from __future__ import annotations

import pytest


def test_prod_missing_bitget_creds_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("BITGET_API_KEY", raising=False)
    monkeypatch.delenv("BITGET_API_SECRET", raising=False)
    monkeypatch.delenv("BITGET_API_PASSPHRASE", raising=False)
    # Ensure CCXT mapping does not fill in unintentionally
    monkeypatch.setenv("CCXT_EXCHANGE_ID", "binance")
    from intradyne.core.config import load_settings

    with pytest.raises(RuntimeError):
        _ = load_settings()


def test_prod_allows_when_creds_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BITGET_API_KEY", "bg_abcdef")
    monkeypatch.setenv("BITGET_API_SECRET", "s3cr3t")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "passphrase")
    from intradyne.core.config import load_settings

    s = load_settings()
    assert s.BITGET_API_KEY.startswith("bg_")


def test_ccxt_mapping_fills_bitget_when_exchange_is_bitget(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("BITGET_API_KEY", raising=False)
    monkeypatch.delenv("BITGET_API_SECRET", raising=False)
    monkeypatch.setenv("CCXT_EXCHANGE_ID", "bitget")
    monkeypatch.setenv("CCXT_API_KEY", "bg_map1234")
    monkeypatch.setenv("CCXT_SECRET", "mapsecret")
    from intradyne.core.config import load_settings

    s = load_settings()
    assert s.BITGET_API_KEY == "bg_map1234"
    assert s.BITGET_API_SECRET == "mapsecret"


def test_redaction_helper():
    from intradyne.core.logging import redact_secrets

    data = {
        "BITGET_API_KEY": "bg_abcdef1234",
        "nested": {"secret": "abcd1234", "ok": "value"},
        "tokens": [
            {"apiToken": "tok12345"},
            {"not_secret": "fine"},
        ],
    }
    red = redact_secrets(data)
    assert red["BITGET_API_KEY"].endswith("****") and red["BITGET_API_KEY"].startswith(
        "bg_a"
    )
    assert red["nested"]["secret"].endswith("****")
    assert red["tokens"][0]["apiToken"].endswith("****")
