from __future__ import annotations

import logging
import os
from typing import Any, Dict

import orjson


def _redact(obj: Any) -> Any:
    sensitive = {"key", "secret", "token", "password"}
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            kl = k.lower()
            if any(s in kl for s in sensitive):
                sv = v if isinstance(v, str) else str(v)
                out[k] = sv[: max(0, min(4, len(sv)))] + "****"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.args:
            payload["args"] = _redact(record.args)
        return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def setup_logging(level: str | None = None) -> None:
    lvl_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(lvl)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
