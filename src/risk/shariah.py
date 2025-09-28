from __future__ import annotations

from typing import Iterable, Optional, Tuple, Any, Dict


class ShariahPolicy:
    def __init__(
        self,
        allowed_crypto: Optional[Iterable[str]] = None,
        blocked_tags: Optional[Iterable[str]] = None,
    ):
        from src.risk.guardrails import ShariahPolicy as _Sh

        # Delegate to existing implementation for compatibility
        self._impl = _Sh(allowed_crypto=allowed_crypto, blocked_tags=blocked_tags)

    def check(
        self, symbol: str, meta: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        return self._impl.check(symbol, meta)


__all__ = ["ShariahPolicy"]
