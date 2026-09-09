"""`/engine/status` reports how prices arrive and how often.

The strategies size their windows in ticks, so the feed's achieved interval is
what converts a 60-tick lookback into a span of time: 60s on the socket, 170s
on a slow REST pass against a 120s time stop. That number decided most of what
went wrong in this engine, and until now it was only observable from outside
the process as the *absence* of a log warning.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from intradyne.api.app import app
from intradyne.engine import loop as engine_loop
from intradyne.engine.data_ws import DataFeed


client = TestClient(app)


def _status():
    r = client.get("/engine/status")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_fields_are_present_even_with_no_engine_running(monkeypatch):
    """A missing key and a null are different answers. Absent, a caller
    cannot tell "not running" from "this build predates the field"."""
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: None)
    body = _status()
    assert "transport" in body and body["transport"] is None
    assert "interval_s" in body and body["interval_s"] is None


def test_the_socket_transport_is_reported(monkeypatch):
    feed = DataFeed(exchange_id="bitget")
    feed.transport = "websocket"
    feed.interval_s = 1.0
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: feed)

    body = _status()
    assert body["transport"] == "websocket"
    assert body["interval_s"] == 1.0


def test_a_slow_rest_pass_is_visible(monkeypatch):
    """2.8s was the measured REST figure at six symbols. At that interval a
    60-tick window spans 168s against a 120s time stop -- the window outlives
    the stop, and this is the field that shows it."""
    feed = DataFeed(exchange_id="bitget")
    feed.transport = "rest"
    feed.interval_s = 2.8
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: feed)

    body = _status()
    assert body["transport"] == "rest"
    assert body["interval_s"] == 2.8
    assert 60 * body["interval_s"] > 120, "the window outlives the time stop"


def test_the_interval_is_rounded_not_truncated(monkeypatch):
    feed = DataFeed(exchange_id="bitget")
    feed.interval_s = 1.0004999
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: feed)
    assert _status()["interval_s"] == 1.0


def test_a_feed_that_has_not_completed_a_pass_reports_no_interval(monkeypatch):
    """`interval_s` is None until the first pass finishes. Reporting 0 there
    would read as an infinitely fast feed."""
    feed = DataFeed(exchange_id="bitget")
    assert feed.interval_s is None
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: feed)
    body = _status()
    assert body["interval_s"] is None
    assert body["transport"] == "rest", "the default before any pass"


def test_the_existing_fields_are_untouched(monkeypatch):
    monkeypatch.setattr(engine_loop, "get_active_feed", lambda: None)
    body = _status()
    for key in (
        "enabled",
        "running",
        "mode",
        "live_trading_enabled",
        "symbols",
        "open_positions",
    ):
        assert key in body, f"{key} disappeared from the status contract"
