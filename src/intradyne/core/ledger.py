from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Tuple

import orjson


class Ledger:
    """Append-only, hash-chained JSONL ledger.

    Each record carries ``hash_prev`` (the previous record's ``hash``) and its
    own ``hash``, computed over the whole record minus ``hash`` with sorted
    keys. Tampering with, reordering, or dropping any record breaks the chain
    from that point on -- see :meth:`verify_chain`.

    Two call styles are supported because the API and the engine grew separate
    ledgers before they were merged:

        ledger.append("order_blocked", {"symbol": ...})   # API
        ledger.append({"event": "fill", "symbol": ...})   # engine

    Concurrency: a lock guards the cached chain head against concurrent
    appends within this process. Multiple *processes* writing one ledger file
    is not supported and would fork the chain.
    """

    def __init__(self, path: str = "guardrails_ledger.jsonl") -> None:
        self.path = Path(path)
        parent = self.path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._lock = threading.Lock()
        # Read once at construction rather than rescanning the file on every
        # append, which made writes O(n) and the ledger O(n^2) to fill.
        self._last_hash: str = self._read_last_hash()

    # ---- internals ----------------------------------------------------

    def _read_last_hash(self) -> str:
        last = ""
        try:
            with self.path.open("rb") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        last = orjson.loads(line).get("hash", "") or ""
                    except Exception:
                        continue
        except FileNotFoundError:
            return ""
        return last

    @staticmethod
    def _hash_record(rec: Mapping[str, Any]) -> str:
        base = {k: v for k, v in rec.items() if k != "hash"}
        return hashlib.sha256(
            orjson.dumps(base, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()

    # ---- writing ------------------------------------------------------

    def append(
        self,
        event: str | Mapping[str, Any],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if isinstance(event, Mapping):
            if payload is not None:
                raise TypeError("pass either a record mapping or (event, payload)")
            record: Dict[str, Any] = dict(event)
            event_name = str(record.pop("event", "record"))
        else:
            record = dict(payload or {})
            event_name = event

        rec: Dict[str, Any] = {
            "ts": record.pop("ts", None) or datetime.utcnow().isoformat() + "Z",
            "event": event_name,
        }
        rec.update(record)

        with self._lock:
            rec["hash_prev"] = self._last_hash
            rec["hash"] = self._hash_record(rec)
            line = orjson.dumps(rec, option=orjson.OPT_SORT_KEYS).decode("utf-8")
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._last_hash = rec["hash"]
        return rec

    # ---- reading ------------------------------------------------------

    def iter_all(self) -> Iterator[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        yield orjson.loads(line)
                    except Exception:
                        continue
        except FileNotFoundError:
            return

    def iter_recent(self, since: datetime) -> Iterable[Dict[str, Any]]:
        for rec in self.iter_all():
            try:
                ts = datetime.fromisoformat(str(rec.get("ts", "")).rstrip("Z"))
            except Exception:
                continue
            if ts >= since:
                yield rec

    def verify_chain(self) -> Tuple[bool, Optional[int], str]:
        """Check every record's hash and its link to the previous one.

        Returns ``(ok, first_bad_index, reason)``. The chain was written from
        the start but nothing ever checked it, so a tampered or truncated
        ledger read as authentic.
        """
        prev = ""
        for i, rec in enumerate(self.iter_all()):
            stored = rec.get("hash")
            if not isinstance(stored, str) or not stored:
                return False, i, "record has no hash"
            if rec.get("hash_prev", "") != prev:
                return False, i, "hash_prev does not match previous record's hash"
            if self._hash_record(rec) != stored:
                return False, i, "record content does not match its hash"
            prev = stored
        return True, None, "ok"


# The engine used this name before the two ledgers were merged.
ExplainabilityLedger = Ledger


__all__ = ["Ledger", "ExplainabilityLedger"]
