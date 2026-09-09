"""Not everything typed "Stock" is common stock, and the name does not always say.

The approach 1 draw pulled 120 names from the A3 universe and four of the
unreachable ones were preferred lines -- `SCHW-P-D`, `TRTN-P-C`, `TY-P`,
`RILYP` -- plus a closed-end fund (`PDX`) and two ADRs (`WX`, `DOYU`). Every
one carries `assetType == "Stock"`, and the name-based filter could not see
them: a preferred line is *named after its issuer*, so `SCHW-P-D` reads
"Charles Schwab Corp" and passes every word check there is.

614 such lines survived into the universe on that basis.

The rules that catch them are cheap to get wrong in the other direction, which
is the harder half. `TRUST` would reject American Assets Trust and Arbor Realty
Trust -- REITs, and ordinary common stock, 284 names. `PORTFOLIO` would reject
Altisource Portfolio Solutions and Consumer Portfolio Service, which are
operating companies. Both were measured against the real universe and left out.

So the tests come in pairs: each rule catches what it is for, and leaves alone
the near-miss that would have been the expensive mistake.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from screen_equities import (  # noqa: E402
    FUND_WRAPPER,
    instrument_type,
    is_preferred_ticker,
)


def ref(symbol: str, name: str, asset: str = "Stock", exch: str = "NYSE") -> dict:
    return {symbol: {"name": name, "assetType": asset, "exchange": exch}}


def verdict(symbol: str, name: str, **kw) -> str:
    return instrument_type(symbol, ref(symbol, name, **kw))[0]


# ---- preferred lines: the ticker is the only evidence --------------------


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("SCHW-P-D", "Charles Schwab Corp"),
        ("TRTN-P-C", "Triton International Ltd"),
        ("TY-P", "Tri-Continental Corp"),
        ("AAM-P-A", "Apollo Asset Management Inc"),
    ],
)
def test_a_nyse_preferred_is_excluded_despite_an_ordinary_name(symbol, name):
    """These names are the issuer's. No word in them is wrong."""
    assert verdict(symbol, name) == "excluded"


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("RILYP", "B. Riley Financial Inc"),
        ("ACGLP", "Arch Capital Group Ltd"),
        ("CBSHP", "Commerce Bancshares Inc"),
    ],
)
def test_a_nasdaq_fifth_letter_p_preferred_is_excluded(symbol, name):
    assert verdict(symbol, name, exch="NASDAQ") == "excluded"


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("ACV-A", "ACV Auctions Inc Class A"),
        ("AKO-B", "Embotelladora Andina S.A."),
        ("AGM-A", "Federal Agricultural Mortgage Corp - Class A"),
    ],
)
def test_share_classes_are_not_mistaken_for_preferred(symbol, name):
    """199 of these exist in the universe. A rule keyed on a trailing letter
    rather than on P would have taken every one."""
    assert verdict(symbol, name) == "common"


@pytest.mark.parametrize("sym", ["SCHW-P-D", "TY-P", "RILYP", "ACGLP"])
def test_preferred_ticker_shapes_are_recognised(sym):
    assert is_preferred_ticker(sym) is True


@pytest.mark.parametrize("sym", ["AAPL", "ACV-A", "AKO-B", "BRK-B", "MSFT", "SNAP"])
def test_ordinary_tickers_are_not(sym):
    assert is_preferred_ticker(sym) is False


def test_a_four_letter_ticker_ending_in_p_is_not_preferred():
    """The NASDAQ convention is a *fifth* letter. SNAP is four and ordinary."""
    assert is_preferred_ticker("SNAP") is False


# ---- fund wrappers -------------------------------------------------------


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("PDX", "PIMCO Energy and Tactical Credit Opportunities Fund"),
        ("WX", "WuXi PharmaTech Cayman Inc. ADR"),
        ("AAAC", "COLUMBIA AAA CLO ETF"),
        ("BITW", "Bitwise 10 Crypto Index Fund"),
    ],
)
def test_fund_and_depositary_wrappers_are_excluded(symbol, name):
    assert verdict(symbol, name) == "excluded"


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("AAT", "American Assets Trust Inc"),
        ("ABR", "Arbor Realty Trust Inc"),
        ("ASPS", "Altisource Portfolio Solutions S.A."),
        ("CPSS", "Consumer Portfolio Service Inc"),
    ],
)
def test_the_near_misses_survive(symbol, name):
    """The expensive mistake. TRUST would take 284 names including two REITs
    that are ordinary common stock; PORTFOLIO would take two operating
    companies. Both words were measured and deliberately excluded."""
    assert verdict(symbol, name) == "common"


@pytest.mark.parametrize("word", ["TRUST", "PORTFOLIO", "INDEX"])
def test_the_dangerous_words_are_not_in_the_rule(word):
    """Pinned so a later tidy-up cannot add them back without a failure."""
    assert word not in FUND_WRAPPER


# ---- when-issued, joined and spaced --------------------------------------


def test_a_spaced_when_issued_line_is_excluded():
    assert verdict("AA-W", "Alcoa Corporation When Issued") == "excluded"


def test_a_joined_wheniussed_line_is_excluded():
    """The gap. Whole-word matching sees one token, WHENISSUED, so a rule
    looking for ISSUED missed every name written without the space."""
    assert verdict("ADIG-W", "ADI Global Distribution Inc WhenIssued") == "excluded"


# ---- ordinary stock is still ordinary ------------------------------------


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("AAPL", "Apple Inc"),
        ("LOW", "Lowe's Companies Inc"),
        ("BKR", "Baker Hughes Company"),
        ("AMCR", "Amcor plc"),
    ],
)
def test_common_stock_passes(symbol, name):
    """LOW, BKR and AMCR end in W, R and R -- the reason the filter reads names
    rather than ticker suffixes in the first place."""
    assert verdict(symbol, name, exch="NASDAQ") == "common"


def test_an_unknown_symbol_is_unknown_not_common():
    """No information is not the same as clean."""
    assert instrument_type("ZZZZ", {})[0] == "unknown"
