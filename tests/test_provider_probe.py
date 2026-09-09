"""The probe's verdicts, and the one signature that cannot be checked live.

Two different things are tested here.

The **probe logic** is testable outright: whether a flat line is recognised as a
placeholder rather than a price series, whether a single-venue feed fails the
breadth comparison, whether a refusal and a placeholder are told apart. Those
decide what the script concludes about a provider, so they are pinned.

The **Webull signature** cannot be verified without their keys and a live
endpoint. What can be pinned are the structural properties of the documented
algorithm -- the `&` appended to the secret, alphabetical ordering,
whole-string URL encoding, and which headers are excluded from signing. Each is
a detail whose absence produces a signature that is wrong in a way no local test
would otherwise notice, because the only symptom is a 401 from a server we
cannot reach from here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from probe_provider import (  # noqa: E402
    MIN_VOLUME_SHARE,
    REFERENCE,
    WEBULL_SIGNED_HEADERS,
    Result,
    _dead_volume,
    _flat,
    _webull_sign,
)


# ---- placeholder detection ------------------------------------------------


def test_a_flat_line_is_recognised_as_a_placeholder():
    """ADVM's shape: 100 sessions of an identical close. Scored naively it is a
    zero-volatility asset rather than a delisting."""
    bars = [(f"t{i}", 4.36, 0.0) for i in range(20)]
    assert _flat(bars) is True


def test_a_real_series_is_not_flagged():
    bars = [(f"t{i}", 4.36 + i * 0.01, 1000.0) for i in range(20)]
    assert _flat(bars) is False


def test_zero_volume_throughout_is_a_placeholder():
    bars = [(f"t{i}", 4.30 + i * 0.01, 0.0) for i in range(20)]
    assert _dead_volume(bars) is True


def test_a_couple_of_quiet_bars_is_not_a_placeholder():
    """A halt or a thin session is not a dead ticker; the check needs length."""
    assert _flat([("t0", 4.36, 0.0), ("t1", 4.36, 0.0)]) is False


# ---- breadth threshold ----------------------------------------------------


def test_iex_share_fails_the_breadth_bar():
    """IEX is ~2.5% of US volume. The whole point of the probe is that this
    fails, because 2.5% of the tape still looks like valid bars."""
    assert 0.025 < MIN_VOLUME_SHARE


def test_a_consolidated_feed_clears_it():
    assert REFERENCE["consolidated_volume"] / REFERENCE["consolidated_volume"] >= (
        MIN_VOLUME_SHARE
    )


# ---- verdict rendering ----------------------------------------------------


@pytest.mark.parametrize("passed,mark", [(True, "PASS"), (False, "FAIL"), (None, "??")])
def test_an_unanswered_probe_is_not_a_pass(passed, mark):
    """None must never render as PASS: 'the probe could not answer' and 'the
    provider is fine' are opposite conclusions."""
    assert Result("n", "q", passed, "").mark == mark


# ---- webull signature: structure, not a live check -----------------------


def _canonical(secret, path, params):
    merged = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return quote(f"{path}&{merged}", safe="")


def test_the_secret_is_suffixed_with_an_ampersand():
    """Documented, easy to miss, and its absence yields a well-formed signature
    that is simply wrong -- a 401 with nothing locally to point at."""
    path, params = "/p", {"a": "1"}
    got = _webull_sign("SECRET", path, params)
    with_amp = base64.b64encode(
        hmac.new(
            b"SECRET&", _canonical("", path, params).encode(), hashlib.sha256
        ).digest()
    ).decode()
    without = base64.b64encode(
        hmac.new(
            b"SECRET", _canonical("", path, params).encode(), hashlib.sha256
        ).digest()
    ).decode()
    assert got == with_amp
    assert got != without


def test_parameters_are_sorted_not_insertion_ordered():
    a = _webull_sign("s", "/p", {"b": "2", "a": "1"})
    b = _webull_sign("s", "/p", {"a": "1", "b": "2"})
    assert a == b


def test_the_canonical_string_is_url_encoded_whole():
    """The separators themselves are encoded, so a signature built over an
    unencoded string differs."""
    sig = _webull_sign("s", "/openapi/market-data/stock/bars", {"symbol": "AAPL"})
    raw = base64.b64encode(
        hmac.new(
            b"s&",
            "/openapi/market-data/stock/bars&symbol=AAPL".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    assert sig != raw


def test_the_signature_and_version_headers_are_not_signed():
    """Documented explicitly: x-signature and x-version do not participate.
    Including x-signature would be circular; including x-version would fail."""
    assert "x-signature" not in WEBULL_SIGNED_HEADERS
    assert "x-version" not in WEBULL_SIGNED_HEADERS


def test_every_documented_signing_header_is_present():
    for h in (
        "x-app-key",
        "x-signature-algorithm",
        "x-signature-nonce",
        "x-signature-version",
        "x-timestamp",
    ):
        assert h in WEBULL_SIGNED_HEADERS


def test_the_signature_is_base64_not_hex():
    sig = _webull_sign("s", "/p", {"a": "1"})
    assert base64.b64decode(sig)
    assert len(base64.b64decode(sig)) == 32
