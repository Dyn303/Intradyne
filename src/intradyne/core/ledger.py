from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

import orjson


class Ledger:
    """Append-only JSONL ledger with hash chaining."""

    def __init__(self, path: str = "guardrails_ledger.jsonl") -> None:
        self.path = path
        if not os.path.exists(self.path):
            open(self.path, "a", encoding="utf-8").close()

    def _last_hash(self) -> Optional[str]:
        last = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            last = orjson.loads(line).get("hash")
                        except Exception:
                            continue
        except FileNotFoundError:
            return None
        return last

    def append(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self._last_hash()
        rec: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event,
        }
        rec.update(payload)
        rec["hash_prev"] = prev or ""
        rec["hash"] = self._hash_record(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(
                orjson.dumps(rec, option=orjson.OPT_SORT_KEYS).decode("utf-8") + "\n"
            )
        return rec

    def iter_recent(self, since: datetime) -> Iterable[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = orjson.loads(line)
                    except Exception:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(rec.get("ts", "")).rstrip("Z"))
                    except Exception:
                        continue
                    if ts >= since:
                        yield rec
        except FileNotFoundError:
            return

    @staticmethod
    def _hash_record(rec: Dict[str, Any]) -> str:
        # Exclude self-hash if present to compute stable content hash
        if "hash" in rec:
            base = {k: v for k, v in rec.items() if k != "hash"}
        else:
            base = rec
        data = orjson.dumps(base, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(data).hexdigest()


__all__ = ["Ledger"]
