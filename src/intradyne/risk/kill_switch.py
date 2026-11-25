"""Durable operator trading halt shared by all order entry points.

The halt lives here, in the risk layer, rather than in the API layer, because
``Guardrails.gate_trade`` consults it. That makes it apply to *every* order
path -- API-submitted and strategy-generated alike -- instead of only to the
routes that remember to ask.

This is the operator's manual switch. It is distinct from the automatic
kill-switch inside ``Guardrails``, which halts on N breaches in 24h.
"""

from __future__ import annotations

from functools import lru_cache

from intradyne.core.alerts import alert
from intradyne.core.config import load_settings
from intradyne.core.db import Backend, open_backend

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_halt (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    reason TEXT NOT NULL
)
"""


class HaltStorageUnavailable(RuntimeError):
    """The halt state could not be safely read or written."""


@lru_cache(maxsize=16)
def _backend(db_url: str) -> Backend:
    db = open_backend(db_url)
    with db.connect() as conn:
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO operator_halt (id, enabled, reason) VALUES (1, 0, '') "
            "ON CONFLICT (id) DO NOTHING"
        )
    return db


def read_halt() -> tuple[bool, str]:
    try:
        with _backend(load_settings().db_url).connect() as conn:
            row = conn.execute(
                "SELECT enabled, reason FROM operator_halt WHERE id = 1"
            ).fetchone()
        if row is None or row[0] not in (0, 1) or not isinstance(row[1], str):
            raise HaltStorageUnavailable("invalid operator halt record")
        return bool(row[0]), row[1]
    except Exception as exc:
        raise HaltStorageUnavailable("operator halt storage unavailable") from exc


def set_halt(enabled: bool, reason: str = "") -> None:
    try:
        with _backend(load_settings().db_url).connect() as conn:
            previous = conn.execute(
                "UPDATE operator_halt SET enabled = enabled WHERE id = 1 RETURNING enabled"
            ).fetchone()
            if previous is None:
                raise HaltStorageUnavailable("missing operator halt record")
            conn.execute(
                "UPDATE operator_halt SET enabled = ?, reason = ? WHERE id = 1",
                (int(enabled), reason if enabled else ""),
            )
    except Exception as exc:
        raise HaltStorageUnavailable("operator halt storage unavailable") from exc
    if enabled and not previous[0]:
        alert("halt_engaged", {"reason": reason or "unspecified"})


def is_halted() -> bool:
    try:
        return read_halt()[0]
    except HaltStorageUnavailable:
        return True


def halt_reason() -> str:
    try:
        return read_halt()[1]
    except HaltStorageUnavailable:
        return "operator_halt_storage_unavailable"


__all__ = ["set_halt", "is_halted", "halt_reason"]
