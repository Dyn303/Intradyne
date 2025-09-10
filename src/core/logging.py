from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        base: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, separators=(",", ":"))


def setup_logging(level: str | None = None) -> None:
    lvl = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(lvl)


_SECRET_KEY_RE = re.compile(r"(key|secret|pass|token|pwd|passwd)", re.IGNORECASE)


def _mask(val: Any) -> Any:
    try:
        s = str(val)
        if not s:
            return "****"
        return s[:4] + "****"
    except Exception:
        return "****"


def redact_secrets(obj: Any) -> Any:
    """Return a redacted copy of obj with likely secret values masked.

    - Dict keys matching _SECRET_KEY_RE are masked.
    - For sequences, redact each element.
    - For other types, return as-is.
    """
    if isinstance(obj, dict):
        out: Dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                # If value is a simple scalar, mask it; for nested structures, recurse instead
                if isinstance(v, (str, bytes, int, float, bool)) or v is None:
                    out[k] = _mask(v)
                else:
                    out[k] = redact_secrets(v)
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_secrets(v) for v in obj)
    if isinstance(obj, set):
        return {redact_secrets(v) for v in obj}
    return obj


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets in record.msg and record.args if they are structures."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            if isinstance(record.args, dict):
                record.args = redact_secrets(record.args)  # type: ignore[assignment]
            if isinstance(record.msg, (dict, list, tuple, set)):
                record.msg = redact_secrets(copy.deepcopy(record.msg))  # type: ignore[assignment]
        except Exception:
            pass
        return True


__all__ = ["setup_logging", "JsonFormatter"]
