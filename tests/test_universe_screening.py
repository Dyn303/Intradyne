"""The screening worksheet feeds a compliance decision, so a
misclassification here is worse than a crash: it produces a confident, wrong
list that someone acts on.

Two earlier versions of this classifier failed silently. One matched
substrings, which put Bitcoin in "DEX" (``dex`` matches "Index") and Chainlink
in "lending". The other forced a single label per token, so Aave -- a lending
protocol that also issues a stablecoin -- was filed under "stablecoin" and its
lending flag vanished. Both are pinned below.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_universe import (  # noqa: E402
    FLAGS,
    LEVERAGED,
    classify,
    worksheet,
)


def _row(base, categories, **kw):
    return {
        "base": base,
        "vol": 1e6,
        "listed": "2020-01-01",
        "years": 5.0,
        "name": base,
        "mcap": 1e9,
        "categories": categories,
        **kw,
    }


# ---- flags -------------------------------------------------------------


def test_a_pure_layer_one_raises_no_flag():
    r = classify(
        [
            _row(
                "BTC",
                ["Smart Contract Platform", "Layer 1 (L1)", "Proof of Work (PoW)"],
            )
        ]
    )[0]
    assert r["flags"] == []
    assert "L1" in r["what"]


def test_a_token_carries_every_flag_that_applies():
    """Aave is a lending protocol that also issues a stablecoin and pays
    yield. Collapsing that to one label loses what the ruling needs."""
    r = classify(
        [
            _row(
                "AAVE",
                [
                    "Decentralized Finance (DeFi)",
                    "Yield Farming",
                    "Lending/Borrowing Protocols",
                    "Stablecoins",
                ],
            )
        ]
    )[0]
    assert set(r["flags"]) == {"LEND", "YIELD", "STABLE", "DEFI"}


def test_an_oracle_is_not_mistaken_for_a_lender():
    """Chainlink carries a DeFi tag; that must not make it a lending
    protocol, which substring matching previously did."""
    r = classify(
        [_row("LINK", ["Infrastructure", "Decentralized Finance (DeFi)", "Oracle"])]
    )[0]
    assert "LEND" not in r["flags"]
    assert "oracle" in r["what"]


def test_an_index_membership_does_not_read_as_an_exchange():
    """`dex` is a substring of "Index". Whole-tag matching is the fix."""
    r = classify([_row("BTC", ["Layer 1 (L1)", "GMCI 30 Index", "Coinbase 50 Index"])])[
        0
    ]
    assert r["flags"] == []


def test_ecosystem_tags_do_not_confer_flags():
    """Nearly every token carries some chain-ecosystem tag; they say nothing
    about the issuer's business."""
    r = classify(
        [
            _row(
                "SOL",
                [
                    "Layer 1 (L1)",
                    "Solana Ecosystem",
                    "BNB Chain Ecosystem",
                    "FTX Holdings",
                ],
            )
        ]
    )[0]
    assert r["flags"] == []


def test_a_memecoin_is_flagged_even_when_tagged_as_a_platform():
    r = classify([_row("DOGE", ["Smart Contract Platform", "Meme", "Dog-Themed"])])[0]
    assert "MEME" in r["flags"]


# ---- the unknown bucket ------------------------------------------------


def test_a_token_with_no_categories_is_unknown_not_clean():
    """The distinction that matters most: absence of evidence read as a pass
    would quietly admit unscreened tokens."""
    r = classify([_row("MYSTERY", [], mcap=None, name=None)])[0]
    assert r["known"] is False
    assert r["flags"] == []


def test_the_worksheet_separates_unknown_from_unflagged():
    rows = classify(
        [
            _row("BTC", ["Layer 1 (L1)"]),
            _row("MYSTERY", [], mcap=None, name=None),
        ]
    )
    body = worksheet(rows, 3.0, 300_000)
    unknown = body.index("## Unknown")
    flagged = body.index("## Flagged")
    assert body.index("MYSTERY") > unknown
    assert body.index("MYSTERY") < flagged
    assert "not clean, they are unidentified" in body


def test_the_worksheet_states_it_is_not_a_ruling():
    """The document must not be mistakable for a compliance sign-off."""
    body = worksheet(classify([_row("BTC", ["Layer 1 (L1)"])]), 3.0, 300_000)
    assert "not a compliance ruling" in body


# ---- table hygiene -----------------------------------------------------


def test_every_flag_documents_the_question_it_raises():
    for name, keys, why in FLAGS:
        assert keys, f"{name} matches nothing"
        assert why.strip(), f"{name} has no stated reason"


def test_flag_names_are_unique():
    names = [f for f, _, _ in FLAGS]
    assert len(names) == len(set(names))


def test_leveraged_token_suffixes_are_declared():
    """Leveraged tokens are derivatives in a spot wrapper and must never
    reach the universe."""
    assert set(LEVERAGED) == {"UP", "DOWN", "BULL", "BEAR"}
    for suffix in LEVERAGED:
        assert f"ETH{suffix}".endswith(suffix)


@pytest.mark.parametrize(
    "tag,flag",
    [
        ("Perpetuals", "DERIV"),
        ("Gambling (GambleFi)", "GAMBLE"),
        ("Exchange-based Tokens", "CEX"),
        ("Privacy", "PRIV"),
        ("Real World Assets (RWA)", "RWA"),
        ("Gaming (GameFi)", "GAME"),
    ],
)
def test_each_screening_category_is_detected(tag, flag):
    assert flag in classify([_row("X", [tag])])[0]["flags"]
