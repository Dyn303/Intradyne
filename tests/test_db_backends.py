"""The SQLite/Postgres seam.

Two kinds of test here. The first kind needs no database: URL classification,
placeholder rewriting and the dialect differences are pure functions, and they
are where a backend swap goes quietly wrong -- a scheme silently defaulting to
the wrong engine, or SQL that runs on one and means something else on the
other.

The second kind needs a live Postgres and is skipped without one. Set
``TEST_POSTGRES_URL`` to run it, e.g.

    TEST_POSTGRES_URL=postgresql://intradyne:intradyne@localhost:5432/intradyne

Those tests are the only proof that the three stores behave identically on both
backends, so CI for the Postgres path is not real until that variable is set.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from intradyne.core import db
from intradyne.core.equity import EquityHistory
from intradyne.core.idempotency import DuplicateOrder, OrderKeyStore
from intradyne.core.limits import NotionalTracker

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set TEST_POSTGRES_URL to exercise the Postgres backend"
)


# --- classification -------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("sqlite:///data/trades.sqlite", "sqlite"),
        ("sqlite:////app/state/trades.sqlite", "sqlite"),
        ("postgresql://u:p@postgres:5432/intradyne", "postgres"),
        # libpq's older spelling, still handed out by hosting providers.
        ("postgres://u:p@localhost/intradyne", "postgres"),
        # SQLAlchemy's driver suffix, in case a DSN is copied from elsewhere.
        ("postgresql+psycopg://u@h/d", "postgres"),
    ],
)
def test_scheme_selects_the_backend(url, expected):
    assert db.backend_for(url) == expected


def test_an_unrecognised_url_is_refused_rather_than_defaulted():
    """A typo in DB_URL must not silently pick a backend.

    Defaulting would mean the store holding the drawdown history quietly
    pointed somewhere else -- guardrails re-arming from an empty database is
    the exact failure equity.py exists to prevent, and it would look like a
    healthy service.
    """
    with pytest.raises(db.UnsupportedDatabase):
        db.backend_for("mysql://user@host/db")
    with pytest.raises(db.UnsupportedDatabase):
        db.backend_for("postgres-typo://host/db")


def test_sqlalchemy_driver_suffix_is_stripped_for_libpq():
    assert db.normalize_dsn("postgresql+psycopg://u@h/d") == "postgresql://u@h/d"
    assert db.normalize_dsn("postgresql://u@h/d") == "postgresql://u@h/d"


# --- dialect --------------------------------------------------------------


def test_placeholders_are_rewritten_outside_string_literals():
    """The stores write '?' once; psycopg needs '%s'.

    The literal matters: 'in_flight' and 'unixepoch' appear in these queries,
    and a rewriter that did not track quoting would be one edit away from
    corrupting one.
    """
    assert (
        db.to_pyformat("SELECT a FROM t WHERE b = ? AND c = 'in_flight' AND d = ?")
        == "SELECT a FROM t WHERE b = %s AND c = 'in_flight' AND d = %s"
    )
    assert db.to_pyformat("SELECT ? FROM t WHERE s = 'a?b'") == (
        "SELECT %s FROM t WHERE s = 'a?b'"
    )


def test_a_percent_sign_is_refused_rather_than_mangled():
    """psycopg would read it as a placeholder. Failing loudly beats guessing."""
    with pytest.raises(ValueError):
        db.to_pyformat("SELECT * FROM t WHERE name LIKE 'BTC%'")


def test_the_day_expression_differs_per_backend():
    """date(ts,'unixepoch') has no Postgres equivalent, and the naive
    translation buckets by server time. This deployment runs Asia/Kuching, so
    that would shift every daily close by eight hours and silently change the
    daily-return series the VaR guardrail reads."""
    sqlite_expr = db.SqliteBackend.day_expr(
        db.SqliteBackend.__new__(db.SqliteBackend), "ts"
    )
    assert sqlite_expr == "date(ts, 'unixepoch')"

    pg = db.PostgresBackend.__new__(db.PostgresBackend)
    assert "AT TIME ZONE 'UTC'" in pg.day_expr("ts")
    # No '%' in the format string, so to_pyformat() does not have to escape it.
    assert "%" not in pg.day_expr("ts")


def test_schema_scripts_split_into_statements():
    assert db.split_statements(
        "CREATE TABLE a (x INT);\n\nCREATE INDEX i ON a (x);\n"
    ) == [
        "CREATE TABLE a (x INT)",
        "CREATE INDEX i ON a (x)",
    ]


def test_postgres_schemas_use_double_precision():
    """Postgres REAL is single precision. An equity value rounded in the
    seventh significant digit is inside the range a drawdown percentage is
    computed over, and an order quantity rounded there would hash to a
    different idempotency key and defeat the duplicate check."""
    from intradyne.core.equity import _SCHEMA as equity_schema
    from intradyne.core.idempotency import _SCHEMA as key_schema
    from intradyne.core.limits import _SCHEMA as limit_schema

    for schema in (equity_schema, key_schema, limit_schema):
        pg = schema["postgres"]
        assert "DOUBLE PRECISION" in pg
        # 'REAL' must not survive into the Postgres DDL.
        assert not any(
            line.strip().split()[1:2] == ["REAL"]
            for line in pg.splitlines()
            if line.strip()
        )


# --- readiness ------------------------------------------------------------


def test_probe_does_not_create_a_missing_sqlite_database(tmp_path):
    """A readiness probe must observe, never provision. The original could not
    fail because it created what it was reporting on."""
    path = tmp_path / "absent.sqlite"
    assert db.probe(f"sqlite:///{path}") is True  # parent is writable
    assert not path.exists(), "the probe created the database it was measuring"


def test_probe_reports_an_unreachable_postgres():
    """The branch this replaced was `db_ok = True  # skip for non-sqlite`.
    Once DB_URL can name a network service, that is the same unfalsifiable
    check in a new place."""
    with pytest.raises(Exception):
        db.probe("postgresql://nobody@127.0.0.1:1/nothing")


def test_readyz_is_not_ready_when_postgres_is_unreachable(monkeypatch):
    from fastapi.testclient import TestClient

    from intradyne.api.app import app
    from intradyne.core.config import reset_settings_cache

    monkeypatch.setenv("DB_URL", "postgresql://nobody@127.0.0.1:1/nothing")
    reset_settings_cache()
    r = TestClient(app).get("/readyz")
    assert r.status_code == 503 or r.json()["components"]["db"] is False


# --- live Postgres --------------------------------------------------------


@pytest.fixture
def pg_url():
    """A Postgres database with the three tables emptied.

    Truncating rather than creating a schema per test: these are the real
    tables, so a test that passes here is evidence about the deployed shape.
    """
    import psycopg

    with psycopg.connect(db.normalize_dsn(POSTGRES_URL)) as conn:
        for table in ("equity_history", "order_keys", "traded_notional"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    yield POSTGRES_URL
    db.close_pools()


@requires_postgres
def test_equity_history_round_trips_on_postgres(pg_url):
    h = EquityHistory(pg_url)
    now = datetime.now(timezone.utc)
    h.record(100.0, now - timedelta(hours=2))
    h.record(90.0, now - timedelta(hours=1))

    assert [round(v, 1) for _, v in h.series_since(now - timedelta(days=1))] == [
        100.0,
        90.0,
    ]
    assert h.latest() == 90.0
    assert h.count() == 2


@requires_postgres
def test_daily_closes_bucket_by_utc_on_postgres(pg_url):
    """The container runs TZ=Asia/Kuching. A day expression that used server
    local time would put a 23:00 UTC observation in the following day."""
    h = EquityHistory(pg_url)
    base = datetime(2026, 3, 1, 23, 30, tzinfo=timezone.utc)
    h.record(100.0, base)
    h.record(110.0, base + timedelta(hours=1))  # next UTC day

    closes = h.daily_closes(days=3650)
    assert [d for d, _ in closes] == ["2026-03-01", "2026-03-02"]


@requires_postgres
def test_a_duplicate_claim_raises_duplicateorder_on_postgres(pg_url):
    """The regression this guards: in Postgres the failed INSERT aborts the
    transaction, so looking the status up on the same connection raises
    InFailedSqlTransaction. The duplicate would surface as a driver error
    rather than DuplicateOrder, and the caller would retry a live order it
    should have refused."""
    store = OrderKeyStore(pg_url)
    store.reserve("idy-1", "BTC/USDT", "buy", 1.0)

    with pytest.raises(DuplicateOrder) as excinfo:
        store.reserve("idy-1", "BTC/USDT", "buy", 1.0)
    assert "in_flight" in str(excinfo.value)

    store.complete("idy-1", "venue-abc")
    assert store.status("idy-1") == "submitted"


@requires_postgres
def test_in_flight_survives_a_new_store_on_postgres(pg_url):
    """A claim outliving the process is the whole point."""
    OrderKeyStore(pg_url).reserve("idy-2", "ETH/USDT", "buy", 2.0)
    assert [r["key"] for r in OrderKeyStore(pg_url).in_flight()] == ["idy-2"]


@requires_postgres
def test_notional_caps_sum_on_postgres(pg_url):
    tracker = NotionalTracker(pg_url)
    now = datetime.now(timezone.utc)
    tracker.record("BTC/USDT", 100.0, now)
    tracker.record("BTC/USDT", 50.0, now)
    tracker.record("ETH/USDT", 25.0, now)
    tracker.record("BTC/USDT", 999.0, now - timedelta(hours=48))  # outside window

    assert tracker.symbol_notional("BTC/USDT", hours=24.0) == 150.0
    assert tracker.total_notional(hours=24.0) == 175.0


@requires_postgres
def test_prune_reports_rows_removed_on_postgres(pg_url):
    tracker = NotionalTracker(pg_url)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    tracker.record("BTC/USDT", 10.0, old)
    tracker.record("BTC/USDT", 10.0, datetime.now(timezone.utc))
    assert tracker.prune(keep_days=30) == 1


@requires_postgres
def test_readyz_probe_passes_against_a_live_postgres(pg_url):
    assert db.probe(pg_url) is True
