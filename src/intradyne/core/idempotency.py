"""Idempotency keys for live order submission.

A live order is not safe to retry blindly. If the process dies between sending
an order and recording the response -- or a network error hides a submission
that actually succeeded -- a naive retry places the order twice, and on a spot
venue that is real money spent twice.

Each submission gets a deterministic client order id derived from the order's
intent. The id is recorded locally *before* the venue is contacted, so a
duplicate is refused even if the process died mid-flight, and it is sent to the
venue as `clientOrderId` so the exchange can reject the duplicate too. Two
independent defences, because either alone has a window.

Paper trading does not use this: nothing is at stake and the paper broker is
in-process.

The backing store is chosen by ``DB_URL``; see :mod:`intradyne.core.db`. On
either backend the primary key on ``key`` is what does the work -- the insert
*is* the claim, so two concurrent submissions cannot both win.
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, Optional

from intradyne.core.db import open_backend

_SCHEMA: Dict[str, str] = {
    "sqlite": """
CREATE TABLE IF NOT EXISTS order_keys (
    key        TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    side       TEXT NOT NULL,
    qty        REAL NOT NULL,
    ts         REAL NOT NULL,
    status     TEXT NOT NULL,
    venue_id   TEXT
);
""",
    # DOUBLE PRECISION for the same reason as the equity table: Postgres REAL
    # is single precision, and an order quantity rounded in the seventh digit
    # would make a replayed intent hash to a different key and defeat the
    # duplicate check.
    "postgres": """
CREATE TABLE IF NOT EXISTS order_keys (
    key        TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    side       TEXT NOT NULL,
    qty        DOUBLE PRECISION NOT NULL,
    ts         DOUBLE PRECISION NOT NULL,
    status     TEXT NOT NULL,
    venue_id   TEXT
);
""",
}

#: Orders with identical intent inside one bucket collapse to one key. Sized so
#: a retry storm dedupes while genuinely repeated signals still get through.
DEFAULT_BUCKET_SECONDS = 30.0


def make_key(
    symbol: str,
    side: str,
    qty: float,
    strategy_id: str = "",
    at: Optional[float] = None,
    bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
) -> str:
    """A deterministic id for one order intent.

    Deterministic rather than random so that the *same* intent replayed after
    a crash produces the same key and is recognised as a duplicate.
    """
    now = time.time() if at is None else at
    bucket = int(now // bucket_seconds) if bucket_seconds > 0 else 0
    raw = f"{symbol}|{side.lower()}|{qty:.10g}|{strategy_id}|{bucket}"
    return "idy-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class DuplicateOrder(Exception):
    """Raised when an order with this key was already submitted."""


class OrderKeyStore:
    def __init__(self, db_url: str = "sqlite:///data/trades.sqlite") -> None:
        self._db = open_backend(db_url)
        self._db.init_schema(_SCHEMA)

    def reserve(self, key: str, symbol: str, side: str, qty: float) -> None:
        """Claim a key before contacting the venue.

        Raises DuplicateOrder if it is already claimed. The insert is the
        claim, so two concurrent submissions cannot both win.
        """
        try:
            with self._db.connect() as conn:
                conn.execute(
                    "INSERT INTO order_keys (key, symbol, side, qty, ts, status) "
                    "VALUES (?, ?, ?, ?, ?, 'in_flight')",
                    (key, str(symbol), str(side), float(qty), time.time()),
                )
        except self._db.integrity_errors:
            # The status lookup is deliberately *outside* the connect block
            # above. In Postgres a failed statement aborts the transaction, so
            # querying the same connection after catching the error raises
            # InFailedSqlTransaction rather than returning the row -- the
            # duplicate would surface as an unrelated driver error instead of
            # as DuplicateOrder, and the caller would retry a live order it
            # should have refused. Leaving the block first rolls back and
            # returns a clean connection; the same code is correct on SQLite.
            status = self.status(key) or "unknown"
            raise DuplicateOrder(
                f"order {key} already submitted (status={status})"
            ) from None

    def complete(self, key: str, venue_id: Optional[str]) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE order_keys SET status = 'submitted', venue_id = ? WHERE key = ?",
                (str(venue_id) if venue_id is not None else None, key),
            )

    def fail(self, key: str) -> None:
        """Mark a claim failed.

        Deliberately keeps the row rather than deleting it: if the venue was
        reached and the response was lost, the order may exist. A human
        reconciles; the system does not silently free the key for reuse.
        """
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE order_keys SET status = 'failed' WHERE key = ?", (key,)
            )

    def status(self, key: str) -> Optional[str]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM order_keys WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def in_flight(self) -> list[dict]:
        """Claims that never completed -- what a restart must reconcile."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT key, symbol, side, qty, ts FROM order_keys "
                "WHERE status = 'in_flight' ORDER BY ts"
            ).fetchall()
        return [
            {"key": k, "symbol": s, "side": d, "qty": q, "ts": t}
            for k, s, d, q, t in rows
        ]


__all__ = ["OrderKeyStore", "DuplicateOrder", "make_key", "DEFAULT_BUCKET_SECONDS"]
