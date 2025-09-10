from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Optional

import orjson
from loguru import logger


class ExplainabilityLedger:
    """Append-only JSONL with hash chaining for tamper-evident logs."""

    def __init__(self, path: str = "logs/ledger.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash: str = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self.path.exists():
            return ""
        last: Optional[str] = None
        try:
            with self.path.open("rb") as f:
                for line in f:
                    last = line.decode("utf-8", "ignore").strip()
            if last:
                try:
                    obj = orjson.loads(last)
                    return obj.get("hash", "")
                except Exception:
                    return ""
        except Exception:
            return ""
        return ""

    @staticmethod
    def _hash_record(prev_hash: str, record: Dict[str, Any]) -> str:
        h = hashlib.sha256()
        h.update(prev_hash.encode("utf-8"))
        h.update(orjson.dumps(record))
        return h.hexdigest()

    def append(self, record: Dict[str, Any]) -> None:
        # Do not allow mutation of provided dict
        payload = dict(record)
        payload["prev_hash"] = self._last_hash
        payload["hash"] = self._hash_record(self._last_hash, record)
        line = orjson.dumps(payload).decode("utf-8")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._last_hash = payload["hash"]
        logger.bind(event="ledger_write").info(payload)


