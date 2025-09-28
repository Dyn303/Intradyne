from __future__ import annotations

from pathlib import Path
from typing import List, Dict

from src.core.ledger import Ledger


def test_ledger_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path=str(path))

    # Append several records
    recs: List[Dict] = []
    for i in range(5):
        recs.append(led.append("event", {"i": i}))

    # Reload and validate chaining by reading the file
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5

    prev_hash = ""
    for line in lines:
        import orjson

        rec = orjson.loads(line)
        # Check that hash_prev matches previous hash
        assert rec.get("hash_prev", "") == (prev_hash or "")
        # Recompute hash and compare
        from src.core.ledger import Ledger as _L

        assert rec["hash"] == _L._hash_record(rec)
        prev_hash = rec["hash"]
