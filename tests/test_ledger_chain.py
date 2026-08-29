from __future__ import annotations

from pathlib import Path
from typing import List, Dict

from intradyne.core.ledger import Ledger


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
        from intradyne.core.ledger import Ledger as _L

        assert rec["hash"] == _L._hash_record(rec)
        prev_hash = rec["hash"]


def test_verify_chain_accepts_an_untampered_ledger(tmp_path: Path) -> None:
    led = Ledger(path=str(tmp_path / "l.jsonl"))
    for i in range(5):
        led.append("event", {"i": i})
    assert led.verify_chain() == (True, None, "ok")


def test_verify_chain_detects_edited_content(tmp_path: Path) -> None:
    """The chain was always written but never checked, so an edited ledger
    used to read as authentic."""
    path = tmp_path / "l.jsonl"
    led = Ledger(path=str(path))
    for i in range(5):
        led.append("event", {"i": i})

    import orjson

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    rec = orjson.loads(lines[2])
    rec["i"] = 999  # tamper, leaving the stored hash untouched
    lines[2] = orjson.dumps(rec, option=orjson.OPT_SORT_KEYS).decode("utf-8")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, idx, reason = Ledger(path=str(path)).verify_chain()
    assert ok is False and idx == 2
    assert "does not match its hash" in reason


def test_verify_chain_detects_a_removed_record(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    led = Ledger(path=str(path))
    for i in range(5):
        led.append("event", {"i": i})

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, idx, reason = Ledger(path=str(path)).verify_chain()
    assert ok is False and idx == 2
    assert "hash_prev" in reason


def test_engine_style_single_dict_append(tmp_path: Path) -> None:
    """The engine passes one record dict; the API passes (event, payload).
    Both write the same chained format."""
    led = Ledger(path=str(tmp_path / "l.jsonl"))
    a = led.append({"event": "fill", "symbol": "BTC/USDT", "qty": 1.0})
    b = led.append("order_blocked", {"symbol": "ETH/USDT"})
    assert a["event"] == "fill" and a["symbol"] == "BTC/USDT"
    assert b["event"] == "order_blocked"
    assert b["hash_prev"] == a["hash"]
    assert led.verify_chain()[0] is True


def test_engine_record_without_event_key(tmp_path: Path) -> None:
    """execution.py writes trade records that carry no 'event' field."""
    led = Ledger(path=str(tmp_path / "l.jsonl"))
    rec = led.append({"symbol": "BTC/USDT", "qty": 2.0, "mode": "live"})
    assert rec["event"] == "record"
    assert led.verify_chain()[0] is True


def test_chain_head_survives_reopening(tmp_path: Path) -> None:
    """The head is cached in memory; a second instance must pick it up from
    disk rather than restarting the chain."""
    path = tmp_path / "l.jsonl"
    first = Ledger(path=str(path))
    a = first.append("event", {"i": 0})
    second = Ledger(path=str(path))
    b = second.append("event", {"i": 1})
    assert b["hash_prev"] == a["hash"]
    assert second.verify_chain()[0] is True
