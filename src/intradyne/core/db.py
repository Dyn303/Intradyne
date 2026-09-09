"""Storage backend selection: SQLite or Postgres, chosen by the ``DB_URL`` scheme.

Three stores share one database -- equity history, order idempotency keys and
traded notional. All three were written against ``sqlite3`` directly, which
worked until the database had to live somewhere a Docker Desktop bind mount
could not host: WAL needs shared-memory mapping of the ``-shm`` sidecar, the
mount cannot provide it, and writes fail with a bare "disk I/O error" while
``/readyz`` -- which only ran ``SELECT 1`` -- kept reporting the database
healthy. See the comment in ``deploy/docker-compose.yml``.

This module is the seam that lets the same store classes run on either engine.
``sqlite:///...`` keeps the existing behaviour; ``postgresql://...`` uses a
pooled psycopg connection. Nothing else in the codebase changes, and switching
back is one environment variable -- which is the point. The SQLite path is not
deprecated: it is still the default in ``config.py``, still what the tests run
against, and still the right choice for a single-process laptop deployment.

Deliberately not SQLAlchemy. The SQL here is nine statements over three tables;
an ORM would be more code than it replaces, and the existing modules are
written in plain SQL a reader can check against the schema.

Statements are written once, in SQLite's ``?`` placeholder style, and rewritten
for psycopg's ``%s`` on the way through. The two dialects genuinely differ in
three places, and those are handled explicitly rather than papered over:

* ``REAL`` is 8-byte in SQLite and 4-byte in Postgres. An equity value stored
  as Postgres ``REAL`` loses precision around the seventh significant digit,
  which is inside the range a drawdown percentage is computed from. The
  Postgres schemas therefore say ``DOUBLE PRECISION``, and each store spells
  its schema out per backend rather than generating one from the other.
* ``date(ts, 'unixepoch')`` has no Postgres equivalent; see :meth:`day_expr`.
* A failed statement aborts the whole transaction in Postgres, so a caller
  cannot catch an integrity error and then query the same connection. Callers
  must leave the ``connect()`` block before their follow-up query.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Protocol, Sequence, Tuple, Type

from loguru import logger

#: URL schemes routed to each backend. ``postgres://`` is libpq's older
#: spelling and is still what several hosting providers hand out, so both are
#: accepted.
_SQLITE_SCHEMES = frozenset({"sqlite", "sqlite3"})
_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})


class UnsupportedDatabase(ValueError):
    """DB_URL named a backend this build cannot talk to."""


def backend_for(db_url: str) -> str:
    """``"sqlite"`` or ``"postgres"`` for a DB_URL. Raises otherwise.

    A URL we cannot classify is an error rather than a default. Defaulting
    would mean a typo in DB_URL silently selected a backend, and for the store
    holding the drawdown history "silently selected something else" is the
    whole class of bug this database exists to prevent.
    """
    scheme = db_url.split("://", 1)[0].split("+", 1)[0].strip().lower()
    if scheme in _SQLITE_SCHEMES:
        return "sqlite"
    if scheme in _POSTGRES_SCHEMES:
        return "postgres"
    raise UnsupportedDatabase(
        f"unsupported database url {db_url!r}: expected a sqlite:// or "
        f"postgresql:// scheme, got {scheme!r}"
    )


def normalize_dsn(db_url: str) -> str:
    """A libpq-acceptable DSN from a possibly SQLAlchemy-flavoured URL.

    ``postgresql+psycopg://host/db`` is valid SQLAlchemy and invalid libpq.
    Accepting it costs one line and removes a confusing connection error for
    anyone who copied a DSN out of another project.
    """
    scheme, sep, rest = db_url.partition("://")
    if not sep:
        return db_url
    return f"{scheme.split('+', 1)[0]}://{rest}"


def to_pyformat(sql: str) -> str:
    """Rewrite ``?`` placeholders to psycopg's ``%s``.

    A literal ``%`` would also need escaping for psycopg, and silently mangling
    one is worse than refusing it, so a statement containing ``%`` is rejected.
    Nothing here uses one: the Postgres day expression spells its format as
    ``'YYYY-MM-DD'`` specifically so that it does not. If that changes, escape
    it here deliberately.
    """
    if "%" in sql:
        raise ValueError(
            f"SQL containing '%' needs explicit psycopg escaping; refusing to guess: {sql!r}"
        )
    out: list[str] = []
    in_literal = False
    for ch in sql:
        if ch == "'":
            in_literal = not in_literal
            out.append(ch)
        elif ch == "?" and not in_literal:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def split_statements(script: str) -> list[str]:
    """Split a schema script into statements.

    ``sqlite3`` has ``executescript``; psycopg does not, and its handling of
    multiple statements in one call differs enough to be worth not relying on.
    """
    return [s.strip() for s in script.split(";") if s.strip()]


class _Cursor(Protocol):  # pragma: no cover - structural typing only
    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...

    @property
    def rowcount(self) -> int: ...


class Backend:
    """What the stores use in place of a raw ``sqlite3`` connection."""

    #: ``"sqlite"`` or ``"postgres"``; stores index their schema dict on it.
    name: str = ""

    #: Exception types meaning "a unique constraint rejected this row". The
    #: order-key store treats one as proof a duplicate order was refused.
    integrity_errors: Tuple[Type[BaseException], ...] = ()

    def connect(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def init_schema(self, schema: Dict[str, str]) -> None:
        with self.connect() as conn:
            for statement in split_statements(schema[self.name]):
                conn.execute(statement)

    def day_expr(self, column: str) -> str:
        """SQL rendering a unix timestamp as a ``YYYY-MM-DD`` UTC date string.

        Both forms return text in the same format, so ``daily_closes`` gets
        comparable keys either way. Postgres' ``to_timestamp`` yields a
        ``timestamptz``; the ``AT TIME ZONE 'UTC'`` is what makes the bucket
        UTC rather than the server's timezone, which in this deployment is
        Asia/Kuching and would shift every daily close by eight hours.
        """
        if self.name == "sqlite":
            return f"date({column}, 'unixepoch')"
        return f"to_char(to_timestamp({column}) AT TIME ZONE 'UTC', 'YYYY-MM-DD')"


# --- SQLite ---------------------------------------------------------------

#: Set once when the filesystem refuses WAL, so the fallback is not retried
#: (and not re-logged) on every connection.
_WAL_UNAVAILABLE = False


def sqlite_path_from_url(db_url: str) -> str:
    """Filesystem path from a sqlite URL.

    Handles both ``sqlite:///relative/path.db`` and the four-slash absolute
    form ``sqlite:////abs/path.db``.
    """
    if not db_url.startswith("sqlite"):
        raise ValueError(f"not a sqlite url: {db_url}")
    return db_url.split("sqlite:///")[-1]


class SqliteBackend(Backend):
    name = "sqlite"
    integrity_errors = (sqlite3.IntegrityError,)

    def __init__(self, db_url: str) -> None:
        self.path = sqlite_path_from_url(db_url)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Serialises this store's operations, as each store used to do for
        # itself. Same granularity as before -- per store, not per file -- so
        # two stores on one database still rely on SQLite's own locking, with
        # the 5s busy timeout below as the backstop. Postgres has no
        # equivalent: a process-wide lock there would serialise every request
        # through one connection and defeat the pool.
        self._lock = threading.Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # A connection per operation: sqlite connections are not shareable
        # across threads, and the write volume here is trivial.
        self._lock.acquire()
        try:
            conn = sqlite3.connect(self.path, timeout=5.0)
        except BaseException:
            self._lock.release()
            raise
        global _WAL_UNAVAILABLE
        if not _WAL_UNAVAILABLE:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                # WAL needs -wal and -shm sidecar files and a filesystem that
                # supports shared memory mapping. Docker bind mounts often
                # provide neither, and the pragma fails with a bare "disk I/O
                # error" that surfaces as a 500 from every risk and portfolio
                # endpoint -- while /readyz, which only runs SELECT 1, keeps
                # reporting the database healthy.
                #
                # WAL is a concurrency optimisation and this database takes a
                # handful of writes a minute, so the rollback journal is a
                # perfectly good fallback. Recorded once: it is a property of
                # the filesystem, not of the connection.
                _WAL_UNAVAILABLE = True
                logger.bind(event="sqlite_wal_unavailable").info(
                    {"path": self.path, "error": str(exc), "journal": "delete"}
                )
        try:
            # Commits on a clean exit, rolls back on an exception.
            with conn:
                yield conn
        finally:
            conn.close()
            self._lock.release()


# --- Postgres -------------------------------------------------------------

#: One pool per DSN, shared by every store pointed at it. Three stores each
#: opening a connection per operation would put three TCP and authentication
#: round trips on a path that used to be a local file read.
_POOLS: Dict[str, Any] = {}
_POOLS_LOCK = threading.Lock()

#: How long a caller waits for a free connection before failing. A database
#: that is down must surface as an error the guardrails can see, not as a
#: request that hangs until the client gives up.
POOL_TIMEOUT_S = 5.0

#: How long a single connection attempt waits. Same reasoning.
CONNECT_TIMEOUT_S = 5


def _import_psycopg() -> Tuple[Any, Any]:
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise UnsupportedDatabase(
            "DB_URL names a Postgres database but psycopg is not installed. "
            "Install it with: pip install 'psycopg[binary,pool]'"
        ) from exc
    return psycopg, ConnectionPool


def _get_pool(dsn: str) -> Any:
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None:
            _psycopg, connection_pool = _import_psycopg()
            pool = connection_pool(
                dsn,
                min_size=1,
                max_size=8,
                timeout=POOL_TIMEOUT_S,
                kwargs={"connect_timeout": CONNECT_TIMEOUT_S},
                open=True,
            )
            _POOLS[dsn] = pool
        return pool


def close_pools() -> None:
    """Close every pooled connection. For tests and for a clean shutdown."""
    with _POOLS_LOCK:
        for pool in _POOLS.values():
            try:
                pool.close()
            except Exception:  # pragma: no cover - best effort on teardown
                pass
        _POOLS.clear()


class _PgConnection:
    """A psycopg connection that accepts the stores' ``?`` placeholder SQL."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        return self._conn.execute(to_pyformat(sql), tuple(params))

    def __getattr__(self, item: str) -> Any:
        return getattr(self._conn, item)


class PostgresBackend(Backend):
    name = "postgres"

    def __init__(self, db_url: str) -> None:
        self.dsn = normalize_dsn(db_url)
        psycopg, _connection_pool = _import_psycopg()
        # Both are listed because psycopg maps the duplicate-key SQLSTATE to
        # UniqueViolation, but a driver-level integrity problem surfaces as the
        # base class, and reserve() must treat either as "already claimed".
        self.integrity_errors = (
            psycopg.errors.UniqueViolation,
            psycopg.IntegrityError,
        )

    @contextmanager
    def connect(self) -> Iterator[_PgConnection]:
        pool = _get_pool(self.dsn)
        # pool.connection() commits on a clean exit and rolls back on an
        # exception -- the same contract as sqlite3's `with conn`.
        with pool.connection() as conn:
            yield _PgConnection(conn)


# --- selection ------------------------------------------------------------


def open_backend(db_url: str) -> Backend:
    """The backend for a DB_URL. Raises UnsupportedDatabase for anything else."""
    if backend_for(db_url) == "sqlite":
        return SqliteBackend(db_url)
    return PostgresBackend(db_url)


def probe(db_url: str) -> bool:
    """Can this database be reached and read, without creating anything?

    Used by ``/readyz``. Deliberately does not go through the pool: a readiness
    probe that queues behind saturated application traffic reports the wrong
    thing, and a probe that provisions what it is measuring cannot fail -- the
    exact defect the SQLite branch of this check was written to fix.
    """
    if backend_for(db_url) == "sqlite":
        path = sqlite_path_from_url(db_url)
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            return False
        if not os.path.exists(path):
            # Not created yet; ready if we could create it on first write.
            return os.access(parent, os.W_OK)
        # mode=rw opens without creating, so a missing or unreadable file is
        # reported rather than silently conjured.
        conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True

    psycopg, _connection_pool = _import_psycopg()
    with psycopg.connect(
        normalize_dsn(db_url), connect_timeout=CONNECT_TIMEOUT_S
    ) as conn:
        conn.execute("SELECT 1")
    return True


__all__ = [
    "Backend",
    "PostgresBackend",
    "SqliteBackend",
    "UnsupportedDatabase",
    "backend_for",
    "close_pools",
    "normalize_dsn",
    "open_backend",
    "probe",
    "split_statements",
    "sqlite_path_from_url",
    "to_pyformat",
]
