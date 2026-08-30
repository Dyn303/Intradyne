"""Shariah compliance policy.

These are the structural rules the product exists to enforce:

* **No riba** -- no margin, leverage or borrowing. Rejected by screening the
  venue parameters for derivative/leverage keys.
* **No selling what you do not own** -- long-only. A sell is permitted only
  against inventory actually held.
* **No gharar** -- spot instruments only; no futures, swaps or perpetuals.
* **Business screening** -- an allow-list of instruments, plus tag exclusion
  for prohibited underlying activity.

The checks used to live in the engine (``engine/compliance.py``) where they
raised exceptions, and separately as a whitelist-only policy in the guardrail
engine. They are unified here so that a single pre-trade gate can apply all of
them and record the outcome, rather than each order path enforcing a different
subset.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple


class ComplianceError(Exception):
    """Raised by the imperative helpers when an order violates policy."""


#: Venue parameters implying margin, leverage or a derivative instrument.
FORBIDDEN_ORDER_PARAMS = (
    "leverage",
    "marginMode",
    "reduceOnly",
    "positionSide",
    "contract",
    "swap",
    "futures",
)

DEFAULT_BLOCKED_TAGS = ("gambling", "riba", "porn")


def is_crypto_symbol(symbol: str) -> bool:
    return "/" in (symbol or "")


# ---- imperative helpers (used at the broker boundary) --------------------


def assert_whitelisted(symbol: str, whitelist: Iterable[str]) -> None:
    if symbol not in whitelist:
        raise ComplianceError(f"Symbol {symbol} not in whitelist; trading blocked.")


def enforce_spot_only(params: Optional[Dict[str, Any]] = None) -> None:
    for k in FORBIDDEN_ORDER_PARAMS:
        if k in (params or {}):
            raise ComplianceError("Non-spot or leveraged parameter detected.")


def forbid_shorting(
    side: str, base_inventory: float, qty: Optional[float] = None
) -> None:
    """Long-only: a sell may not exceed inventory held.

    ``qty`` is optional for backwards compatibility, but omitting it only
    catches selling from a flat position -- selling *more* than is held is
    equally a short and is caught only when ``qty`` is supplied.
    """
    if side.lower() != "sell":
        return
    if base_inventory <= 0:
        raise ComplianceError("Short selling blocked by Shariah compliance.")
    if qty is not None and qty > base_inventory:
        raise ComplianceError(
            f"Sell of {qty} exceeds inventory {base_inventory}; "
            "the excess would be a short position."
        )


# ---- policy object (used by the pre-trade gate) --------------------------


class ShariahPolicy:
    """Declarative form of the rules above, returning a reason instead of
    raising, so the gate can record why an order was refused."""

    def __init__(
        self,
        allowed_crypto: Optional[Iterable[str]] = None,
        blocked_tags: Optional[Iterable[str]] = None,
    ) -> None:
        self.allowed_crypto = set(allowed_crypto or [])
        self.blocked_tags = set(blocked_tags or DEFAULT_BLOCKED_TAGS)

    def check(
        self,
        symbol: str,
        side: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        base_inventory: Optional[float] = None,
        qty: Optional[float] = None,
    ) -> Tuple[bool, str]:
        meta = meta or {}

        # Spot-only. Screened for every instrument, not only crypto pairs.
        for k in FORBIDDEN_ORDER_PARAMS:
            if k in (params or {}):
                return False, f"non-spot or leveraged order parameter: {k}"

        if is_crypto_symbol(symbol):
            if self.allowed_crypto and symbol not in self.allowed_crypto:
                return False, f"Crypto {symbol} not in allowed list"
            tags = meta.get("tags") or []
            blocked = [t for t in tags if t in self.blocked_tags]
            if blocked:
                return False, f"blocked tags: {', '.join(sorted(blocked))}"

        # Long-only.
        if (side or "").lower() == "sell":
            if base_inventory is None:
                # Fail closed: without inventory the sell cannot be shown to
                # be covered, and an uncovered sell is exactly what is barred.
                return False, "cannot verify long-only: inventory unknown"
            if base_inventory <= 0:
                return False, "short selling blocked by Shariah compliance"
            if qty is not None and qty > base_inventory:
                return (
                    False,
                    f"sell of {qty} exceeds inventory {base_inventory}; "
                    "the excess would be a short position",
                )

        return True, "ok"


__all__ = [
    "ComplianceError",
    "ShariahPolicy",
    "FORBIDDEN_ORDER_PARAMS",
    "DEFAULT_BLOCKED_TAGS",
    "assert_whitelisted",
    "enforce_spot_only",
    "forbid_shorting",
    "is_crypto_symbol",
]
