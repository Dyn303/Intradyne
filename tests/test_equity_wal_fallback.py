"""The equity database must open on filesystems that refuse WAL.

Found by bringing the compose stack up rather than by reading anything.
`PRAGMA journal_mode=WAL` needs -wal and -shm sidecar files and a filesystem
that supports shared memory mapping; a Docker bind mount frequently provides
neither, and the pragma fails with a bare "disk I/O error".

The failure mode was the bad kind. Every risk and portfolio endpoint returned
500, while `/readyz` -- which opens the database and runs `SELECT 1`, never
touching the pragma -- kept reporting it healthy.
"""

import sqlite3

import pytest

from intradyne.core import db
from intradyne.core.equity import EquityHistory


@pytest.fixture(autouse=True)
def _reset_flag():
    db._WAL_UNAVAILABLE = False
    yield
    db._WAL_UNAVAILABLE = False


@pytest.fixture
def wal_refused(monkeypatch):
    """A filesystem that rejects the WAL pragma and nothing else.

    sqlite3.Connection is a C type whose methods cannot be replaced, so this
    wraps the connection rather than patching it -- the same reason the real
    failure could only be reproduced by mounting a volume.
    """
    calls = {"wal": 0}
    real_connect = sqlite3.connect

    class Refusing:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *a, **kw):
            if "journal_mode=WAL" in sql:
                calls["wal"] += 1
                raise sqlite3.OperationalError("disk I/O error")
            return self._conn.execute(sql, *a, **kw)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: Refusing(real_connect(*a, **kw))
    )
    return calls


def test_the_database_opens_when_wal_is_refused(tmp_path, wal_refused):
    """This raised on construction, so nothing that touched equity worked."""
    h = EquityHistory(f"sqlite:///{tmp_path / 'trades.sqlite'}")
    assert h._db.path.endswith("trades.sqlite")


def test_reads_and_writes_still_work_without_wal(tmp_path, wal_refused):
    """The fallback has to be a working database, not merely a quiet one."""
    from datetime import datetime, timedelta, timezone

    h = EquityHistory(f"sqlite:///{tmp_path / 'trades.sqlite'}")
    now = datetime.now(timezone.utc)
    h.record(100.0, now - timedelta(hours=2))
    h.record(90.0, now - timedelta(hours=1))

    series = h.series_since(now - timedelta(days=1))
    assert [round(v, 1) for _, v in series] == [100.0, 90.0]


def test_the_pragma_is_not_retried_on_every_connection(tmp_path, wal_refused):
    """A connection per operation means retrying would raise and log on every
    single query. It is a property of the filesystem, not the connection."""
    h = EquityHistory(f"sqlite:///{tmp_path / 'trades.sqlite'}")
    before = wal_refused["wal"]
    for _ in range(5):
        h.record(1.0)
    assert wal_refused["wal"] == before, "WAL was attempted again after failing"


def test_wal_is_still_used_where_it_works(tmp_path):
    """The fallback must not cost WAL on filesystems that support it."""
    h = EquityHistory(f"sqlite:///{tmp_path / 'trades.sqlite'}")
    with h._db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    assert db._WAL_UNAVAILABLE is False


def test_a_genuine_database_error_still_surfaces(tmp_path, monkeypatch):
    """Only the pragma is forgiven. Swallowing every OperationalError would
    turn a broken database into a silently empty one, which is the exact bug
    this module's docstring exists to prevent."""

    def refuse(*a, **kw):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    with pytest.raises(sqlite3.OperationalError):
        EquityHistory(f"sqlite:///{tmp_path / 'trades.sqlite'}")
