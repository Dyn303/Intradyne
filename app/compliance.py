from __future__ import annotations

from typing import Iterable


class ComplianceError(Exception):
    pass


def assert_whitelisted(symbol: str, whitelist: Iterable[str]) -> None:
    if symbol not in whitelist:
        raise ComplianceError(f"Symbol {symbol} not in whitelist; trading blocked.")


def enforce_spot_only(params: dict | None = None) -> None:
    params = params or {}
    # Forbidden keys that suggest margin/derivatives/leverage
    forbidden = [
        "leverage",
        "marginMode",
        "reduceOnly",
        "positionSide",
        "contract",
        "swap",
        "futures",
    ]
    for k in forbidden:
        if k in params:
            raise ComplianceError("Non-spot or leveraged parameter detected.")


def forbid_shorting(side: str, base_inventory: float) -> None:
    # Long-only: sells are allowed only to close existing inventory (no negative inventory)
    if side.lower() == "sell" and base_inventory <= 0:
        raise ComplianceError("Short selling blocked by Shariah compliance.")


