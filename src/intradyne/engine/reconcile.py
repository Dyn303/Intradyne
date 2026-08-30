"""Restart reconciliation for live trading.

An order claimed but never completed is the dangerous state: the process died
between contacting the venue and recording the response, so the order may or
may not exist on the exchange. Local records cannot answer that question.

This deliberately does not guess. It detects unresolved claims and halts
trading, leaving a human to check the venue. Automatically re-sending would
risk doubling a position, and automatically discarding would risk trading
against a position the system does not know it holds -- both worse than
stopping.

Paper mode has nothing at stake and is not gated by this.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from intradyne.core.idempotency import OrderKeyStore
from intradyne.risk.kill_switch import set_halt


#: Claims younger than this are treated as genuinely in flight rather than
#: orphaned, so a reconciliation running alongside live traffic is not
#: tripped by an order that is merely in progress.
IN_FLIGHT_GRACE_SECONDS = 120.0


def find_unreconciled(
    store: OrderKeyStore, grace_seconds: float = IN_FLIGHT_GRACE_SECONDS
) -> List[Dict[str, Any]]:
    """Claims that were never completed and are older than the grace period."""
    import time

    now = time.time()
    return [
        row
        for row in store.in_flight()
        if (now - float(row.get("ts") or 0.0)) > grace_seconds
    ]


def reconcile_on_start(
    store: Optional[OrderKeyStore],
    *,
    live: bool,
    grace_seconds: float = IN_FLIGHT_GRACE_SECONDS,
) -> Dict[str, Any]:
    """Check for unresolved submissions and halt if any are found.

    Returns a summary. Halting is the point: the system refuses to trade until
    someone has confirmed what the venue actually holds.
    """
    if store is None:
        return {"checked": False, "reason": "no order key store", "unreconciled": []}
    if not live:
        # Paper fills are in-process and cannot be half-submitted.
        return {"checked": False, "reason": "not live", "unreconciled": []}

    unresolved = find_unreconciled(store, grace_seconds)
    if not unresolved:
        logger.info("reconcile: no unresolved live submissions")
        return {"checked": True, "unreconciled": [], "halted": False}

    keys = [row["key"] for row in unresolved]
    reason = (
        f"{len(unresolved)} unreconciled live order(s) from a previous run; "
        "verify them on the venue before resuming"
    )
    set_halt(True, reason=reason)
    logger.bind(event="reconcile_halt").error({"reason": reason, "keys": keys})
    return {"checked": True, "unreconciled": unresolved, "halted": True}


__all__ = [
    "IN_FLIGHT_GRACE_SECONDS",
    "find_unreconciled",
    "reconcile_on_start",
]
