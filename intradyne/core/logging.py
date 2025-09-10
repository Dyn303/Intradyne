from __future__ import annotations

from typing import Any, Dict


SENSITIVE_KEYS = {"key", "secret", "token", "password"}


def _mask(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    return value[: max(0, min(4, len(value)))] + "****"


def redact_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        red: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = k.lower()
            if kl == "tokens":
                # Do not mask the container key; recurse into list items
                red[k] = redact_secrets(v)
            elif any(s in kl for s in SENSITIVE_KEYS):
                red[k] = _mask(v if isinstance(v, str) else str(v))
            else:
                red[k] = redact_secrets(v)
        return red
    elif isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    else:
        return obj
