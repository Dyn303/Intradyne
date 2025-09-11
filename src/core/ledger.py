from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional


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
                        last = json.loads(line).get("hash")
        except FileNotFoundError:
            return None
        return last

    def append(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self._last_hash()
        rec = {"ts": datetime.utcnow().isoformat() + "Z", "event": event}
        rec.update(payload)
        rec["hash_prev"] = prev or ""
        rec["hash"] = self._hash_record(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return rec

    def iter_recent(self, since: datetime) -> Iterable[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    try:
                        ts = datetime.fromisoformat(rec["ts"].rstrip("Z"))
                    except Exception:
                        continue
                    if ts >= since:
                        yield rec
        except FileNotFoundError:
            return

    @staticmethod
    def _hash_record(rec: Dict[str, Any]) -> str:
        data = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()


__all__ = ["Ledger"]
