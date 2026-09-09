"""A symbol nobody can classify is not an equity by default.

`is_crypto_symbol` was `"/" in symbol` -- a test for punctuation, not for an
asset class. That was adequate while it gated one screen. Once the equity gate
landed it stopped gating and started *routing*, and a router with no reject
branch sends every malformed input somewhere.

Where it sent them was the more permissive branch. With an unconfigured
allow-list, `PORNCO/USDT` and `AAPL/USD` passed business screening outright,
because a slash was the entire test; meanwhile `BTC` was refused for wanting an
equity screening record.

These pin the three-way classification and, more importantly, that the third
outcome exists at all.
"""

from __future__ import annotations

import pytest

from intradyne.risk.shariah import (
    ScreenResult,
    ShariahPolicy,
    classify_symbol,
    is_crypto_symbol,
)


# ---- the three outcomes ---------------------------------------------------


@pytest.mark.parametrize(
    "sym", ["BTC/USDT", "ETH/USD", "SOL/USDC", "btc/usdt", " BTC/USDT "]
)
def test_pairs_classify_as_crypto(sym):
    assert classify_symbol(sym) == "crypto"


@pytest.mark.parametrize("sym", ["AAPL", "F", "BRK-B", "AKO-A", "BF.B", "aapl"])
def test_tickers_classify_as_equity(sym):
    """Share classes and the dotted form are ordinary common stock and must
    not fall into `unknown`, which refuses."""
    assert classify_symbol(sym) == "equity"


@pytest.mark.parametrize(
    "sym",
    [
        "",
        "   ",
        "FOO//BAR",
        "TOOLONGTICKER",
        "BTC/",
        "/USDT",
        "BTC/USDT/EXTRA",
        "BTC USDT",
    ],
)
def test_nothing_else_is_guessed_at(sym):
    """The branch that did not exist before. Each of these previously routed
    to the equity path purely for lacking a slash."""
    assert classify_symbol(sym) == "unknown"


def test_a_pair_of_the_same_asset_is_not_a_pair():
    """BTC/BTC is a typo, and screening it as tradeable would be worse than
    saying so."""
    assert classify_symbol("BTC/BTC") == "unknown"


# ---- unknown refuses ------------------------------------------------------


@pytest.mark.parametrize("sym", ["", "FOO//BAR", "BTC/BTC", "TOOLONGTICKER"])
def test_an_unclassifiable_symbol_is_refused(sym):
    pol = ShariahPolicy(allowed_crypto=["BTC/USDT"])
    ok, why = pol.check(sym)
    assert ok is False
    assert "cannot classify" in why


def test_the_refusal_names_the_symbol():
    """It lands in the hash-chained ledger and is the only account an auditor
    gets of why an order was refused."""
    _, why = ShariahPolicy(allowed_crypto=["BTC/USDT"]).check("FOO//BAR")
    assert "FOO//BAR" in why


# ---- the exploit this closes ---------------------------------------------


@pytest.mark.parametrize("sym", ["PORNCO/USDT", "AAPL/USD", "TSLA/USDT"])
def test_a_slash_does_not_buy_passage_when_no_allow_list_is_set(sym):
    assert ShariahPolicy().check(sym)[0] is False


@pytest.mark.parametrize("sym", ["PORNCO/USDT", "AAPL/USD"])
def test_a_slash_does_not_buy_passage_when_one_is_set_either(sym):
    assert ShariahPolicy(allowed_crypto=["BTC/USDT"]).check(sym)[0] is False


# ---- the old helper still answers its own question ------------------------


def test_is_crypto_symbol_agrees_with_the_classifier():
    assert is_crypto_symbol("BTC/USDT") is True
    assert is_crypto_symbol("AAPL") is False


def test_is_crypto_symbol_is_false_for_unclassifiable_input():
    """It returns False for `unknown` too, which is why callers must not read
    False as "therefore an equity" -- the reason policy uses `classify_symbol`
    rather than this."""
    assert is_crypto_symbol("FOO//BAR") is False
    assert classify_symbol("FOO//BAR") != "equity"


# ---- the two paths still work ---------------------------------------------


def test_a_listed_pair_passes():
    assert ShariahPolicy(allowed_crypto=["BTC/USDT"]).check("BTC/USDT")[0] is True


def test_a_screened_equity_passes():
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    pol = ShariahPolicy(
        allowed_crypto=["BTC/USDT"],
        equity_screen={"AAPL": ScreenResult(True, today, "AAOIFI-style (test)")},
    )
    assert pol.check("AAPL")[0] is True
