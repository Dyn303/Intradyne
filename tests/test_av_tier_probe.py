"""The probe's job is to tell three lookalikes apart.

Alpha Vantage answers a paid endpoint, an exhausted quota and a healthy series
all with HTTP 200. Only the payload distinguishes them, and two of the three
notices mention premium plans -- so the failure mode that matters is reading
"you are going too fast" as "this endpoint is paid" and reporting the tier
assumption confirmed when nothing was learned.

That is a false pass on the exact question the probe exists to answer, and the
first draft had it. These tests pin the classification, which is pure and needs
no network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import av_tier_probe as probe  # noqa: E402

# Verbatim shapes Alpha Vantage returns. The daily-limit notice is the trap:
# it names premium plans while being a throttle.
RATE_PER_SECOND = (
    '{ "Information": "Thank you for using Alpha Vantage! Please consider '
    "spreading out your free API requests more sparingly (1 request per second). "
    'You may also subscribe to a premium plan." }'
)
RATE_PER_DAY = (
    '{ "Information": "We have detected your API key and our standard API rate '
    "limit is 25 requests per day. Please subscribe to any of the premium plans "
    'at https://www.alphavantage.co/premium/ to remove all daily rate limits." }'
)
PREMIUM_ENDPOINT = (
    '{ "Information": "Thank you for using Alpha Vantage! This is a premium '
    "endpoint. You may subscribe to any of the premium plans at "
    'https://www.alphavantage.co/premium/ to instantly unlock all endpoints." }'
)
CSV_SERIES = "timestamp,open,high,low,close,volume\r\n2023-01-03,381.02,381.02,381.02,381.02,258\r\n"
SPLIT_SERIES = "effective_date,split_factor\r\n2020-08-31,4.0\r\n"


# ---- the trap ------------------------------------------------------------


def test_a_daily_limit_notice_is_throttling_not_a_paywall():
    """It names premium plans, and reading that as "premium" would report the
    tier assumption confirmed by a request that never reached the endpoint."""
    assert probe.classify(RATE_PER_DAY) == probe.THROTTLED


def test_a_per_second_notice_is_throttling():
    assert probe.classify(RATE_PER_SECOND) == probe.THROTTLED


def test_a_genuine_premium_notice_is_not_mistaken_for_throttling():
    assert probe.classify(PREMIUM_ENDPOINT) == probe.PREMIUM


# ---- the ordinary cases --------------------------------------------------


@pytest.mark.parametrize("body", [CSV_SERIES, SPLIT_SERIES])
def test_a_csv_payload_is_a_series(body):
    """Both header shapes the fetcher accepts, kept identical to
    price_source so the probe cannot pass while production fails."""
    assert probe.classify(body) == probe.SERIES


def test_an_unrecognised_payload_is_not_silently_a_pass():
    assert probe.classify('{"Error Message": "Invalid API call"}') == probe.OTHER


def test_an_empty_body_is_not_a_series():
    assert probe.classify("") == probe.OTHER


def test_classification_is_case_insensitive():
    assert probe.classify(RATE_PER_DAY.upper()) == probe.THROTTLED
    assert probe.classify(CSV_SERIES.upper()) == probe.SERIES


# ---- the probe reads its expectations from the module it checks ----------


def test_the_probe_checks_the_modules_own_constants():
    """Restating them here would let the two drift: the probe would keep
    confirming a number the fetcher no longer uses."""
    from intradyne.research.price_source import AV, AV_FUNCTION, COMPACT_SESSIONS

    assert probe.COMPACT_SESSIONS is COMPACT_SESSIONS
    assert probe.AV_FUNCTION is AV_FUNCTION
    assert probe.AV is AV


def test_every_frequency_the_fetcher_knows_is_probed():
    """A frequency added to price_source without a probe would ship
    unverified."""
    assert set(probe.AV_FUNCTION) >= {"daily", "weekly", "monthly"}


# ---- failure reporting ---------------------------------------------------


def test_a_missing_key_exits_two_rather_than_claiming_success(monkeypatch, tmp_path):
    """Exit 0 on an unrun probe would be the worst outcome: a green check that
    verified nothing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(probe, "find_dotenv", lambda **kw: "")
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: False)
    assert probe.main([]) == 2


def test_no_request_is_made_without_a_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(probe, "find_dotenv", lambda **kw: "")
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: False)

    def boom(*a, **kw):
        raise AssertionError("the probe contacted the API with no key")

    monkeypatch.setattr(probe.httpx, "get", boom)
    assert probe.main([]) == 2


def test_a_transport_error_is_reported_not_raised(monkeypatch):
    """A probe that crashes tells you less than one that says what failed."""

    def boom(*a, **kw):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(probe.httpx, "get", boom)
    kind, detail, rows = probe.fetch("k", {"function": "TIME_SERIES_WEEKLY"})
    assert kind == probe.OTHER
    assert "ConnectionError" in detail
    assert rows == 0


def test_the_key_never_appears_in_a_result(monkeypatch):
    """The key travels in the URL, so a naive error path could echo it."""

    class Resp:
        status_code = 200
        text = RATE_PER_DAY

    monkeypatch.setattr(probe.httpx, "get", lambda *a, **kw: Resp())
    kind, detail, _ = probe.fetch("SECRETKEY123", {"function": "TIME_SERIES_WEEKLY"})
    assert kind == probe.THROTTLED
    assert "SECRETKEY123" not in detail
