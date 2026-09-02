"""Durable equity history.

The drawdown and VaR guardrails were wired to a stub returning an empty list.
``dd_30d([])`` is ``0.0``, so the halt could never trigger no matter how far
equity fell.

This history is deliberately on disk rather than in memory. With in-memory
history the guardrails would re-arm from zero on every restart: a service that
had just fallen 25% would come back believing its drawdown was 0.0 and resume
trading. That is the same bug in a new costume, and a crash-restart loop is
exactly when the drawdown halt most needs to hold.
"""

from __future__ import annotations

import os
import sqlite3

from loguru import logger
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS equity_history (
    ts     REAL NOT NULL,
    equity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_history_ts ON equity_history (ts);
"""


def sqlite_path_from_url(db_url: str) -> str:
    """Filesystem path from a sqlite URL.

    Handles both ``sqlite:///relative/path.db`` and the four-slash absolute
    form ``sqlite:////abs/path.db``.
    """
    if not db_url.startswith("sqlite"):
        raise ValueError(f"not a sqlite url: {db_url}")
    return db_url.split("sqlite:///")[-1]


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


#: Set once when the filesystem refuses WAL, so the fallback is not retried
#: (and not re-logged) on every connection.
_WAL_UNAVAILABLE = False


class EquityHistory:
    def __init__(self, db_url: str = "sqlite:///data/trades.sqlite") -> None:
        self.path = sqlite_path_from_url(db_url)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A connection per operation: sqlite connections are not shareable
        # across threads, and the write volume here is trivial.
        conn = sqlite3.connect(self.path, timeout=5.0)
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
        return conn

    def record(self, equity: float, ts: Optional[datetime] = None) -> None:
        when = _epoch(ts) if ts is not None else datetime.now(timezone.utc).timestamp()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO equity_history (ts, equity) VALUES (?, ?)",
                (float(when), float(equity)),
            )

    def series_since(self, since: datetime) -> List[Tuple[datetime, float]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, equity FROM equity_history WHERE ts >= ? ORDER BY ts",
                (_epoch(since),),
            ).fetchall()
        return [(_utc(ts), float(eq)) for ts, eq in rows]

    def series_30d(self) -> List[Tuple[datetime, float]]:
        return self.series_since(datetime.utcnow() - timedelta(days=30))

    def daily_closes(self, days: int = 30) -> List[Tuple[str, float]]:
        """Last equity observed on each UTC day, oldest first."""
        since = datetime.utcnow() - timedelta(days=days)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date(ts, 'unixepoch') AS day, equity
                FROM equity_history
                WHERE ts >= ?
                ORDER BY ts
                """,
                (_epoch(since),),
            ).fetchall()
        closes: dict[str, float] = {}
        for day, equity in rows:
            closes[day] = float(equity)  # later rows overwrite: last wins
        return sorted(closes.items())

    def daily_returns(self, days: int = 30) -> List[float]:
        closes = self.daily_closes(days)
        out: List[float] = []
        for (_, prev), (_, curr) in zip(closes, closes[1:]):
            if prev > 0:
                out.append((curr - prev) / prev)
        return out

    def latest(self) -> Optional[float]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT equity FROM equity_history ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return float(row[0]) if row else None

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM equity_history").fetchone()[0]
            )

    def prune(self, keep_days: int = 365) -> int:
        cutoff = _epoch(datetime.utcnow() - timedelta(days=keep_days))
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM equity_history WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)


__all__ = ["EquityHistory", "sqlite_path_from_url"]
