"""The research record is served read-only, and stays that way.

Two properties are worth pinning. The first is that a client never supplies a
filesystem path: keys index a registry, so traversal is unrepresentable rather
than filtered. The second is that this router computes nothing -- the plan
names scope creep back into strategy search as the project's most likely
failure mode, and a results browser is precisely what invites re-running one.
"""

import json

import pytest
from fastapi.testclient import TestClient

from intradyne.api.routes import research_record as rr


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ENGINE_ENABLED", "false")
    monkeypatch.setenv("API_AUTH_REQUIRED", "0")


@pytest.fixture
def client():
    from intradyne.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the record at a directory this test owns."""
    monkeypatch.setattr(rr, "_root", lambda: str(tmp_path))
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


# ---- the index -----------------------------------------------------------


def test_the_index_lists_every_registered_record(client):
    body = client.get("/research/record").json()
    assert body["count"] == len(rr.REGISTRY)
    assert {r["key"] for r in body["records"]} == set(rr.REGISTRY)


def test_the_index_reports_missing_files_rather_than_hiding_them(client, sandbox):
    """An artifact that was never generated is a fact about the record. A list
    that silently omits it looks like the test was never run."""
    body = client.get("/research/record").json()
    assert all(r["available"] is False for r in body["records"])
    assert all(r["bytes"] == 0 for r in body["records"])


def test_the_index_carries_the_verdict_not_just_the_numbers(client):
    """Several of these look encouraging in one cell and fail overall. Showing
    figures without the conclusion is how a closed question gets reopened."""
    by_key = {r["key"]: r for r in client.get("/research/record").json()["records"]}
    assert "negative" in by_key["cross_sectional_v2"]["verdict"]
    assert "pre-registration" in by_key["cross_sectional_v2"]["verdict"]
    assert "clustered" in by_key["multi_instrument_search"]["verdict"]


# ---- reading one record --------------------------------------------------


def test_a_known_record_returns_its_contents(client, sandbox):
    (sandbox / "artifacts" / "ctrend_test.json").write_text(
        json.dumps({"full_7d": {"pass": False, "sharpe": -0.01}}), encoding="utf-8"
    )
    body = client.get("/research/record/ctrend_test").json()
    assert body["data"]["full_7d"]["pass"] is False
    assert body["source"] == "artifacts/ctrend_test.json"


def test_an_ungenerated_record_is_404_not_500(client, sandbox):
    r = client.get("/research/record/ctrend_test")
    assert r.status_code == 404
    assert r.json()["detail"] == "not_generated"


def test_a_truncated_artifact_says_so(client, sandbox):
    """Half-written JSON should not surface as an opaque 500."""
    (sandbox / "artifacts" / "ctrend_test.json").write_text('{"full', encoding="utf-8")
    r = client.get("/research/record/ctrend_test")
    assert r.status_code == 422
    assert "unreadable" in r.json()["detail"]


# ---- the client never names a path --------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "nope",
        "..",
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "artifacts/ctrend_test.json",
        "/etc/passwd",
        "....//....//secrets",
    ],
)
def test_only_registry_keys_resolve(client, key):
    """None of these may read a file. A 404 either from routing or from the
    registry lookup is the correct answer; anything else is not."""
    r = client.get(f"/research/record/{key}")
    assert r.status_code == 404


def test_a_key_is_never_used_as_a_path(client, sandbox):
    """The decisive check: put a file exactly where a traversal would land and
    confirm no key can reach it."""
    (sandbox / "secret_file.json").write_text('{"leaked": true}', encoding="utf-8")
    for key in ("secret_file", "secret_file.json", "../secret_file.json"):
        r = client.get(f"/research/record/{key}")
        assert r.status_code == 404
        assert "leaked" not in r.text


# ---- the universe timeline ----------------------------------------------


def test_timeline_summary_reports_size_and_churn():
    raw = {
        "2024-01-01": ["BTC", "ETH"],
        "2024-02-01": ["BTC", "ETH", "SOL"],
        "2024-03-01": ["BTC", "SOL"],
    }
    points = rr.summarise_timeline(raw)
    assert [p["size"] for p in points] == [2, 3, 2]
    # The first point has no predecessor, so nothing "entered" at it.
    assert points[0]["added"] == [] and points[0]["removed"] == []
    assert points[1]["added"] == ["SOL"] and points[1]["removed"] == []
    assert points[2]["added"] == [] and points[2]["removed"] == ["ETH"]


def test_timeline_summary_orders_by_date_not_insertion():
    """JSON object order is whatever wrote the file. Churn computed against
    the wrong neighbour is silently, plausibly wrong."""
    raw = {"2024-03-01": ["BTC"], "2024-01-01": ["BTC", "ETH"]}
    points = rr.summarise_timeline(raw)
    assert [p["date"] for p in points] == ["2024-01-01", "2024-03-01"]
    assert points[1]["removed"] == ["ETH"]


def test_the_timeline_endpoint_exposes_the_survivorship_gap(client, sandbox):
    """The gap between ever-listed and currently-listed is exactly the bias a
    backtest over today's symbols would absorb without saying so."""
    (sandbox / "docs" / "universe_timeline.json").write_text(
        json.dumps(
            {
                "2024-01-01": ["BTC", "ETH", "LUNA"],
                "2024-02-01": ["BTC", "ETH"],
            }
        ),
        encoding="utf-8",
    )
    body = client.get("/research/universe/timeline").json()
    assert body["dates"] == 2
    assert body["ever_listed"] == 3
    assert body["current"] == 2
    assert body["delisted"] == ["LUNA"]


def test_an_ungenerated_timeline_is_404(client, sandbox):
    assert client.get("/research/universe/timeline").status_code == 404


# ---- this router does not compute ---------------------------------------


def test_the_record_router_has_no_write_or_compute_routes():
    """routes/research.py next door runs optimisations. This module must stay
    a reader: a browsable results view is exactly what invites re-running a
    search that has already concluded."""
    for route in rr.router.routes:
        assert set(route.methods) <= {"GET", "HEAD"}, f"{route.path} accepts writes"
        assert "optimize" not in route.path and "backtest" not in route.path
