"""Process-wide trading halt.

The halt lives here, in the risk layer, rather than in the API layer, because
``Guardrails.gate_trade`` consults it. That makes it apply to *every* order
path -- API-submitted and strategy-generated alike -- instead of only to the
routes that remember to ask.

This is the operator's manual switch. It is distinct from the automatic
kill-switch inside ``Guardrails``, which halts on N breaches in 24h.
"""

from __future__ import annotations

import threading

from intradyne.core.alerts import alert

_LOCK = threading.Lock()
_HALTED: bool = False
_REASON: str = ""


def set_halt(enabled: bool, reason: str = "") -> None:
    global _HALTED, _REASON
    with _LOCK:
        was = _HALTED
        _HALTED = bool(enabled)
        _REASON = reason if enabled else ""
    # Alert on the transition only, outside the lock. A halt is the single
    # most important thing that can happen to this system unattended, and it
    # is the reason the alerting exists at all. `alert` never raises, so a
    # notification failure cannot leave the halt half-applied.
    if enabled and not was:
        alert("halt_engaged", {"reason": reason or "unspecified"})


def is_halted() -> bool:
    return _HALTED


def halt_reason() -> str:
    return _REASON


__all__ = ["set_halt", "is_halted", "halt_reason"]
