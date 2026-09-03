"""Durable equity history.

The drawdown and VaR guardrails were wired to a stub returning an empty list.
``dd_30d([])`` is ``0.0``, so the halt could never trigger no matter how far
equity fell.

This history is deliberately on disk rather than in memory. With in-memory
history the guardrails would re-arm from zero on every restart: a service that
had just fallen 25% would come back believing its drawdown was 0.0 and resume
trading. That is the same bug in a new costume, and a crash-restart loop is
exactly when the drawdown halt most needs to hold.

Which disk is chosen by ``DB_URL``; see :mod:`intradyne.core.db`. SQLite is the
default and Postgres is available for deployments where a bind-mounted SQLite
file is not workable. The durability argument above is what the two backends
have in common and is the only property this module depends on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from intradyne.core.db import open_backend, sqlite_path_from_url

#: ``REAL`` is 8-byte in SQLite and 4-byte in Postgres, and single precision
#: loses digits inside the range a drawdown percentage is computed over, so the
#: Postgres column is spelled out rather than shared.
_SCHEMA: Dict[str, str] = {
    "sqlite": """
CREATE TABLE IF NOT EXISTS equity_history (
    ts     REAL NOT NULL,
    equity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_history_ts ON equity_history (ts);
""",
    "postgres": """
CREATE TABLE IF NOT EXISTS equity_history (
    ts     DOUBLE PRECISION NOT NULL,
    equity DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_history_ts ON equity_history (ts);
""",
}


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class EquityHistory:
    def __init__(self, db_url: str = "sqlite:///data/trades.sqlite") -> None:
        self._db = open_backend(db_url)
        self._db.init_schema(_SCHEMA)

    def record(self, equity: float, ts: Optional[datetime] = None) -> None:
        when = _epoch(ts) if ts is not None else datetime.now(timezone.utc).timestamp()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO equity_history (ts, equity) VALUES (?, ?)",
                (float(when), float(equity)),
            )

    def series_since(self, since: datetime) -> List[Tuple[datetime, float]]:
        with self._db.connect() as conn:
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
        day = self._db.day_expr("ts")
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {day} AS day, equity
                FROM equity_history
                WHERE ts >= ?
                ORDER BY ts
                """,
                (_epoch(since),),
            ).fetchall()
        closes: Dict[str, float] = {}
        for day_key, equity in rows:
            closes[str(day_key)] = float(equity)  # later rows overwrite: last wins
        return sorted(closes.items())

    def daily_returns(self, days: int = 30) -> List[float]:
        closes = self.daily_closes(days)
        out: List[float] = []
        for (_, prev), (_, curr) in zip(closes, closes[1:]):
            if prev > 0:
                out.append((curr - prev) / prev)
        return out

    def latest(self) -> Optional[float]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT equity FROM equity_history ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return float(row[0]) if row else None

    def count(self) -> int:
        with self._db.connect() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM equity_history").fetchone()[0]
            )

    def prune(self, keep_days: int = 365) -> int:
        cutoff = _epoch(datetime.utcnow() - timedelta(days=keep_days))
        with self._db.connect() as conn:
            cur = conn.execute("DELETE FROM equity_history WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)


__all__ = ["EquityHistory", "sqlite_path_from_url"]
