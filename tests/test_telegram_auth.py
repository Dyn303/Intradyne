"""Mini App auth is the first thing in this project that faces the open
internet, so these tests are weighted toward what must be *refused*.

A Mini App needs a public HTTPS URL -- Telegram will not open localhost. The
moment it is switched on, this API is reachable by anyone, and a valid
signature proves only that the request came from a Telegram user, not from the
owner. So the test that matters most here is not that a good signature is
accepted; it is that a good signature belonging to the wrong person is not,
and that a missing allowlist closes the door rather than opening it.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from intradyne.api import telegram_auth as ta

TOKEN = "8387457203:TEST-ONLY-NOT-A-REAL-CREDENTIAL"
OWNER = 5150
STRANGER = 9999


def make_init_data(
    *,
    token: str = TOKEN,
    user_id: int | None = OWNER,
    auth_date: float | None = None,
    username: str = "owner",
    extra: dict | None = None,
) -> str:
    """Build a correctly signed initData string, the way Telegram does.

    Signing here rather than pasting a captured fixture is deliberate: a
    fixture would go stale against `auth_date` checks, and hand-rolling the
    signature is the only way to prove the verifier rejects a *wrong* one.
    """
    fields: dict[str, str] = {"auth_date": str(int(auth_date or time.time()))}
    if user_id is not None:
        fields["user"] = json.dumps(
            {"id": user_id, "username": username, "first_name": "Test"},
            separators=(",", ":"),
        )
    if extra:
        fields.update(extra)
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


ALLOW = frozenset({OWNER})


def verify(init_data: str, **kw):
    kw.setdefault("token", TOKEN)
    kw.setdefault("allowed", ALLOW)
    return ta.verify_init_data(init_data, **kw)


# ---- the happy path ------------------------------------------------------


def test_a_signature_from_an_allowlisted_user_is_accepted():
    user = verify(make_init_data())
    assert user.id == OWNER
    assert user.label == "@owner"


def test_the_verifier_matches_telegram_field_ordering():
    """Fields are hashed sorted by key, and extra fields Telegram adds must
    not break verification -- `signature` and `chat_instance` are both sent by
    current clients and neither is stripped."""
    data = make_init_data(
        extra={"chat_instance": "-123", "signature": "abc", "query_id": "AAA"}
    )
    assert verify(data).id == OWNER


def test_a_user_with_no_username_still_verifies():
    user = verify(make_init_data(username=""))
    assert user.label == f"id:{OWNER}"


# ---- forgery -------------------------------------------------------------


def test_a_tampered_field_invalidates_the_signature():
    data = make_init_data()
    tampered = data.replace(f"%22id%22%3A{OWNER}", f"%22id%22%3A{STRANGER}")
    assert tampered != data, "test did not actually tamper with anything"
    with pytest.raises(ta.InitDataError) as e:
        verify(tampered)
    assert e.value.reason == "bad_signature"


def test_a_signature_from_a_different_bot_is_rejected():
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(token="1111:SOME-OTHER-BOT"))
    assert e.value.reason == "bad_signature"


def test_unsigned_data_is_rejected():
    with pytest.raises(ta.InitDataError) as e:
        verify(urlencode({"auth_date": str(int(time.time())), "user": "{}"}))
    assert e.value.reason == "unsigned"


def test_empty_init_data_is_rejected():
    with pytest.raises(ta.InitDataError) as e:
        verify("")
    assert e.value.reason == "missing"


def test_an_appended_field_does_not_survive_verification():
    """Adding a field after signing changes the check string, so it must fail
    rather than being silently ignored."""
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data() + "&is_admin=1")
    assert e.value.reason == "bad_signature"


# ---- replay --------------------------------------------------------------


def test_stale_init_data_is_rejected():
    """initData never expires on Telegram's side, so a copy lifted from a log
    would otherwise authenticate forever."""
    old = make_init_data(auth_date=time.time() - 7200)
    with pytest.raises(ta.InitDataError) as e:
        verify(old, max_age=3600)
    assert e.value.reason == "stale"


def test_fresh_init_data_inside_the_window_is_accepted():
    recent = make_init_data(auth_date=time.time() - 60)
    assert verify(recent, max_age=3600).id == OWNER


def test_a_far_future_timestamp_is_rejected():
    """A forged auth_date is the obvious way to defeat a freshness check."""
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(auth_date=time.time() + 86400), max_age=3600)
    assert e.value.reason == "future_dated"


def test_small_clock_skew_is_tolerated():
    assert verify(make_init_data(auth_date=time.time() + 30), max_age=3600).id == OWNER


def test_the_freshness_check_can_be_disabled_deliberately():
    ancient = make_init_data(auth_date=1)
    assert verify(ancient, max_age=0).id == OWNER


# ---- authorisation, not just authentication ------------------------------


def test_a_valid_signature_from_a_stranger_is_refused():
    """The whole point. Telegram will happily sign for any of its users."""
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(user_id=STRANGER))
    assert e.value.reason == "not_allowed"


def test_an_empty_allowlist_admits_nobody():
    """Fail closed. An allowlist that is missing must not mean "everyone" --
    that is the configuration that would silently publish a trading console."""
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(), allowed=frozenset())
    assert e.value.reason == "no_allowlist"


def test_data_with_no_user_object_is_refused():
    """Telegram omits `user` in inline contexts. Nobody to check, nobody in."""
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(user_id=None))
    assert e.value.reason == "no_user"


def test_no_bot_credential_means_no_mini_app_auth():
    with pytest.raises(ta.InitDataError) as e:
        verify(make_init_data(), token="")
    assert e.value.reason == "not_configured"


# ---- configuration -------------------------------------------------------


def test_enabled_requires_both_halves(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    assert ta.enabled() is False, "a credential with no allowlist must not count"
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER))
    assert ta.enabled() is True
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert ta.enabled() is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5150", {5150}),
        ("5150,9999", {5150, 9999}),
        (" 5150 , 9999 ", {5150, 9999}),
        ("5150;9999", {5150, 9999}),
        ("5150,,9999", {5150, 9999}),
        ("", set()),
        ("   ", set()),
        # A typo drops that entry rather than widening the list or crashing.
        ("5150,oops", {5150}),
        ("oops", set()),
    ],
)
def test_allowlist_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", raw)
    assert ta.allowed_user_ids() == frozenset(expected)


def test_a_malformed_max_age_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_INITDATA_MAX_AGE_S", "soon")
    assert ta.max_age_s() == ta.DEFAULT_MAX_AGE_S


# ---- wired into the API --------------------------------------------------


@pytest.fixture
def secured(monkeypatch):
    """An app with auth on and the Mini App configured."""
    monkeypatch.setenv("API_AUTH_REQUIRED", "1")
    monkeypatch.setenv("X_API_KEY", "the-header-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER))
    monkeypatch.setenv("ENGINE_ENABLED", "false")
    from intradyne.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_a_signed_request_reaches_the_api(secured):
    r = secured.get("/risk/status", headers={"X-Telegram-Init-Data": make_init_data()})
    assert r.status_code == 200


def test_an_unauthenticated_request_is_refused(secured):
    assert secured.get("/risk/status").status_code == 401


def test_the_header_key_still_works(secured):
    r = secured.get("/risk/status", headers={"X-API-Key": "the-header-key"})
    assert r.status_code == 200


def test_a_stranger_is_refused_by_the_api(secured):
    r = secured.get(
        "/risk/status",
        headers={"X-Telegram-Init-Data": make_init_data(user_id=STRANGER)},
    )
    assert r.status_code == 401


def test_the_rejection_does_not_say_which_check_failed(secured):
    """ "Valid signature, wrong user" and "forged signature" must look
    identical from outside, or the response becomes an oracle telling an
    attacker whether what they hold is genuine."""
    forged = secured.get(
        "/risk/status", headers={"X-Telegram-Init-Data": make_init_data(token="1:x")}
    )
    stranger = secured.get(
        "/risk/status",
        headers={"X-Telegram-Init-Data": make_init_data(user_id=STRANGER)},
    )
    assert forged.status_code == stranger.status_code == 401
    assert forged.json() == stranger.json()
    body = forged.text.lower()
    for leak in ("signature", "allow", "stale", "expired", "user"):
        assert leak not in body, f"the 401 body leaks {leak!r}"


def test_bad_init_data_does_not_fall_through_to_the_key_path(secured):
    """A rejected signature must be reported as such, not turned into a
    generic "no key supplied" by falling through to the other credential."""
    r = secured.get(
        "/risk/status",
        headers={
            "X-Telegram-Init-Data": make_init_data(user_id=STRANGER),
            "X-API-Key": "the-header-key",
        },
    )
    assert r.status_code == 401, "a bad signature was rescued by the API key"


def test_the_app_boots_with_only_mini_app_auth_configured(monkeypatch):
    """A Mini App deployment need never mint an API key, so the boot-time
    fail-closed check has to accept either credential."""
    monkeypatch.setenv("API_AUTH_REQUIRED", "1")
    monkeypatch.delenv("X_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(OWNER))
    from intradyne.api.app import create_app

    with TestClient(create_app()) as c:
        assert (
            c.get(
                "/risk/status", headers={"X-Telegram-Init-Data": make_init_data()}
            ).status_code
            == 200
        )


def test_the_app_still_refuses_to_boot_with_no_credential_at_all(monkeypatch):
    monkeypatch.setenv("API_AUTH_REQUIRED", "1")
    monkeypatch.delenv("X_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    from intradyne.api.app import create_app

    with pytest.raises(RuntimeError, match="no credential is configured"):
        create_app()
