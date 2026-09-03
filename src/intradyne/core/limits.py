"""Traded-notional tracking, for the exposure caps.

The guardrails bound *risk* (drawdown, VaR, flash crash) but nothing bounded
how much the system could transact. A strategy looping on a bad signal could
place unlimited orders inside every risk threshold, because each one is small.
These caps bound the total.

Durable for the same reason the equity history is: a cap that resets on
restart is not a cap. A crash-restart loop is exactly the situation in which
runaway order flow is most likely, and an in-memory counter would forget the
day's usage each time. Which durable store is chosen by ``DB_URL``; see
:mod:`intradyne.core.db`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from intradyne.core.db import open_backend

#: DOUBLE PRECISION on Postgres rather than REAL: these values are summed over
#: a 24h window and compared against a cap, and single-precision rounding
#: accumulates across the sum in the direction of under-reporting exposure.
_SCHEMA: Dict[str, str] = {
    "sqlite": """
CREATE TABLE IF NOT EXISTS traded_notional (
    ts       REAL NOT NULL,
    symbol   TEXT NOT NULL,
    notional REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traded_notional_ts ON traded_notional (ts);
CREATE INDEX IF NOT EXISTS idx_traded_notional_sym ON traded_notional (symbol, ts);
""",
    "postgres": """
CREATE TABLE IF NOT EXISTS traded_notional (
    ts       DOUBLE PRECISION NOT NULL,
    symbol   TEXT NOT NULL,
    notional DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traded_notional_ts ON traded_notional (ts);
CREATE INDEX IF NOT EXISTS idx_traded_notional_sym ON traded_notional (symbol, ts);
""",
}


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class NotionalTracker:
    def __init__(self, db_url: str = "sqlite:///data/trades.sqlite") -> None:
        self._db = open_backend(db_url)
        self._db.init_schema(_SCHEMA)

    def record(
        self, symbol: str, notional: float, ts: Optional[datetime] = None
    ) -> None:
        if notional <= 0:
            return
        when = _epoch(ts) if ts is not None else datetime.now(timezone.utc).timestamp()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO traded_notional (ts, symbol, notional) VALUES (?, ?, ?)",
                (float(when), str(symbol), float(notional)),
            )

    def symbol_notional(self, symbol: str, hours: float = 24.0) -> float:
        since = _epoch(datetime.utcnow() - timedelta(hours=hours))
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(notional), 0) FROM traded_notional "
                "WHERE symbol = ? AND ts >= ?",
                (str(symbol), since),
            ).fetchone()
        return float(row[0] or 0.0)

    def total_notional(self, hours: float = 24.0) -> float:
        since = _epoch(datetime.utcnow() - timedelta(hours=hours))
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(notional), 0) FROM traded_notional WHERE ts >= ?",
                (since,),
            ).fetchone()
        return float(row[0] or 0.0)

    def prune(self, keep_days: int = 30) -> int:
        cutoff = _epoch(datetime.utcnow() - timedelta(days=keep_days))
        with self._db.connect() as conn:
            cur = conn.execute("DELETE FROM traded_notional WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)


__all__ = ["NotionalTracker"]
