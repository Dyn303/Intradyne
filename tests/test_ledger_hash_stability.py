"""The ledger hash must not move when a dependency is upgraded.

`_hash_record` is `sha256(orjson.dumps(record, OPT_SORT_KEYS))`, so the audit
chain's integrity rests on orjson's byte output being stable. If an upgrade
ever changed that -- key order, float formatting, unicode escaping -- every
previously written chain would stop verifying, and nothing else in the suite
would notice: `test_ledger_chain` recomputes hashes with the same version it
just wrote them with, so it is self-consistent by construction and blind to
this.

The literal below was computed under orjson 3.11.6 and verified unchanged
against 3.11.1. If a future bump breaks it, that is a real finding about the
audit trail and not a test to update casually: existing ledgers on disk would
need re-verification under the old version first.
"""

from intradyne.core.ledger import Ledger

#: A record exercising the cases where JSON encoders actually differ:
#: unsorted keys, nesting, negative zero, exponent-form floats, non-ASCII.
RECORD = {
    "ts": 1756500000.123456,
    "event": "order_filled",
    "symbol": "ETH/USDT",
    "side": "buy",
    "qty": 0.0125,
    "px": 1875.42,
    "pnl": -0.0,
    "features": {"z": -1.5, "atr": 0.0031, "arabic": "حلال"},
    "nested": {"b": [1, 2, {"c": None, "a": True}], "a": {"x": 1e-07}},
    "hash_prev": "0" * 64,
}

GOLDEN = "0e9c4573c5bb247d42a73fea5ea4cc509f66525a58be3c7f6d36f63b14f0028a"


def test_hash_of_a_known_record_is_unchanged():
    assert Ledger._hash_record(RECORD) == GOLDEN


def test_the_stored_hash_field_is_excluded_from_the_hash():
    """Otherwise the hash would depend on itself and no chain could verify."""
    with_hash = dict(RECORD, hash="whatever-was-there-before")
    assert Ledger._hash_record(with_hash) == GOLDEN


def test_key_order_does_not_affect_the_hash():
    """OPT_SORT_KEYS is what makes the chain reproducible across processes."""
    reordered = dict(reversed(list(RECORD.items())))
    assert Ledger._hash_record(reordered) == GOLDEN


def test_a_changed_value_changes_the_hash():
    """The tamper-evidence the ledger exists to provide."""
    assert Ledger._hash_record(dict(RECORD, qty=0.0126)) != GOLDEN
