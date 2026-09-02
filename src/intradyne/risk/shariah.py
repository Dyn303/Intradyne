"""Shariah compliance policy.

These are the structural rules the product exists to enforce:

* **No riba** -- no margin, leverage or borrowing. Rejected by screening the
  venue parameters for derivative/leverage keys.
* **No selling what you do not own** -- long-only. A sell is permitted only
  against inventory actually held.
* **No gharar** -- spot instruments only; no futures, swaps or perpetuals.
* **Business screening** -- an allow-list for crypto, a dated screen record
  for equities, plus tag exclusion for prohibited underlying activity in both.

The checks used to live in the engine (``engine/compliance.py``) where they
raised exceptions, and separately as a whitelist-only policy in the guardrail
engine. They are unified here so that a single pre-trade gate can apply all of
them and record the outcome, rather than each order path enforcing a different
subset.

**Business screening used to apply to crypto only, and failed open.** The check
was guarded by ``if is_crypto_symbol(symbol)`` -- and that function is
``"/" in symbol``. An order for ``AAPL`` therefore skipped the allow-list and
the tag screen entirely and was permitted, rather than being refused for want
of a screen. Nothing caught it because every test uses ``BTC/USDT``-shaped
symbols.

Screening is now unconditional, and the two asset classes are permitted by
different evidence:

* **Crypto** keeps the allow-list, with its existing semantics unchanged: an
  empty allow-list means none was configured and does not by itself refuse.
* **Everything else** -- equity tickers -- requires a dated screen record
  saying the instrument passed a named standard, and refuses when there is
  none. Equities have never been screened here, so there is no prior behaviour
  to preserve, and fail-closed is the only defensible default for an
  instrument nobody has ruled on.

A screen record carries an ``as_of`` date because a financial-ratio screen
expires: the ratios are recomputed each time a company files. A record older
than ``max_screen_age_days`` is treated as no record at all, which mirrors the
posture already taken for unknown inventory below -- refuse, and say why.

Populating that map is a **decision, not an import.**
``scripts/screen_equities.py`` produces a worksheet and states in its own
docstring that it does not decide what is permissible; wiring its output
straight into this gate would quietly promote a worksheet to a ruling. The map
is supplied explicitly by whoever made the ruling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


class ComplianceError(Exception):
    """Raised by the imperative helpers when an order violates policy."""


#: How long a financial-ratio screen stays good for. Ratios move when a
#: company files, so roughly one quarter plus reporting lag.
DEFAULT_MAX_SCREEN_AGE_DAYS = 120


@dataclass(frozen=True)
class ScreenResult:
    """One instrument's screening outcome, and when it was decided.

    ``standard`` is required rather than optional: AAOIFI, DJIM, S&P and MSCI
    screens differ in both thresholds and denominator, so a bare pass/fail does
    not say what was actually applied.
    """

    passed: bool
    as_of: str
    standard: str
    reason: str = ""

    def age_days(self, today: Optional[date] = None) -> Optional[int]:
        """Days since the screen, or None when ``as_of`` is unparseable."""
        try:
            when = datetime.strptime(self.as_of, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
        return ((today or datetime.now(timezone.utc).date()) - when).days


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
        equity_screen: Optional[Mapping[str, ScreenResult]] = None,
        max_screen_age_days: int = DEFAULT_MAX_SCREEN_AGE_DAYS,
    ) -> None:
        self.allowed_crypto = set(allowed_crypto or [])
        self.blocked_tags = set(blocked_tags or DEFAULT_BLOCKED_TAGS)
        #: Ticker -> ScreenResult. Empty means no equity has been ruled on,
        #: which refuses all of them rather than permitting all of them.
        self.equity_screen = {k.upper(): v for k, v in (equity_screen or {}).items()}
        self.max_screen_age_days = max_screen_age_days

    def _equity_permitted(self, symbol: str) -> Tuple[bool, str]:
        """Refuse anything without a current, passing screen on record.

        The reason strings name the symbol and the failure, because they land
        in the hash-chained ledger and are the only account an auditor gets of
        why an order was refused.
        """
        rec = self.equity_screen.get((symbol or "").upper())
        if rec is None:
            return False, f"no Shariah screen on record for {symbol}"
        if not rec.passed:
            detail = f": {rec.reason}" if rec.reason else ""
            return False, f"{symbol} failed the {rec.standard} screen{detail}"
        age = rec.age_days()
        if age is None:
            return False, f"screen for {symbol} has an unreadable date ({rec.as_of!r})"
        if age > self.max_screen_age_days:
            return (
                False,
                f"screen for {symbol} is stale: {age}d old, "
                f"limit {self.max_screen_age_days}d",
            )
        return True, "ok"

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

        # Business screening, applied to every instrument. Which evidence
        # permits it depends on the asset class; that no evidence permits it
        # is refused either way.
        if is_crypto_symbol(symbol):
            if self.allowed_crypto and symbol not in self.allowed_crypto:
                return False, f"Crypto {symbol} not in allowed list"
        else:
            ok, why = self._equity_permitted(symbol)
            if not ok:
                return False, why

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
    "ScreenResult",
    "ShariahPolicy",
    "DEFAULT_MAX_SCREEN_AGE_DAYS",
    "FORBIDDEN_ORDER_PARAMS",
    "DEFAULT_BLOCKED_TAGS",
    "assert_whitelisted",
    "enforce_spot_only",
    "forbid_shorting",
    "is_crypto_symbol",
]
