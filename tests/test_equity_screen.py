"""The instrument-type filter is the compliance boundary, so it is tested
against a real listing response rather than an invented one.

`tests/fixtures/listing_status_sample.csv` holds genuine `LISTING_STATUS` rows
for the tickers that appeared in a live `TOP_GAINERS_LOSERS` response, plus a
handful of ordinary common stocks chosen because a naive suffix rule would
throw them away. Every assertion below is about that real data.

The measurement that motivated the filter: of that response, 14 of 20 top
gainers and 16 of 20 top losers were warrants or rights, and most-active
carried leveraged and inverse ETFs. Screening on price and volume alone hands
the engine a majority-impermissible list.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from screen_equities import (  # noqa: E402
    FLAGS,
    instrument_type,
    words,
)

FIXTURE = Path(__file__).parent / "fixtures" / "listing_status_sample.csv"


@pytest.fixture(scope="module")
def ref():
    rows = {}
    with FIXTURE.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[r["symbol"]] = {
                "name": r["name"],
                "assetType": r["assetType"],
                "exchange": r["exchange"],
            }
    assert rows, "fixture is empty"
    return rows


# Warrants and rights that appeared in the live movers response.
WARRANTS = [
    "GFAIW",
    "PARAW",
    "LCFYW",
    "ASTLW",
    "ARBEW",
    "CINGW",
    "PAACW",
    "BIAFW",
    "SCLXW",
    "NRSNW",
    "OBAWW",
    "RCKTW",
    "DAICW",
    "CUBWW",
    "GIPRW",
    "BFRGW",
    "SPWRW",
    "VWAVW",
    "GCLWW",
    "KIDZW",
    "MRNOW",
    "BZFDW",
    "FGIWW",
]

# Leveraged, inverse or derivative-backed funds from the same response.
DERIVATIVE_FUNDS = ["TSLL", "NVD", "SOXL", "BITO"]

# Ordinary common stock. A rule rejecting tickers ending in W or R would
# wrongly discard every one of these.
COMMON = ["LOW", "BKR", "AMCR", "CR", "R", "NVDA", "PATH", "AAL", "NOK", "PCG"]


@pytest.mark.parametrize("sym", WARRANTS)
def test_warrants_are_excluded(ref, sym):
    verdict, reason = instrument_type(sym, ref)
    assert verdict == "excluded", f"{sym} ({ref[sym]['name']}) survived as {verdict}"
    assert reason


def test_no_warrant_reaches_screening(ref):
    """The headline claim, asserted in one place."""
    survived = [s for s in WARRANTS if instrument_type(s, ref)[0] == "common"]
    assert survived == [], f"warrants reached screening: {survived}"


@pytest.mark.parametrize("sym", DERIVATIVE_FUNDS)
def test_leveraged_and_derivative_funds_never_screen_as_common(ref, sym):
    """A fund is never common equity, whatever it holds.

    TSLL and SOXL are caught by their names ("DAILY ... BULL 2X"). BITO is
    named "PROSHARES BITCOIN STRATEGY ETF" and carries no leverage marker at
    all, so it is caught only by being a fund. Both routes must end outside
    `common`, which is the only verdict that proceeds to screening.
    """
    verdict, _ = instrument_type(sym, ref)
    assert verdict != "common", f"{sym} ({ref[sym]['name']}) screened as common equity"


@pytest.mark.parametrize("sym", COMMON)
def test_ordinary_common_stock_is_not_over_rejected(ref, sym):
    verdict, reason = instrument_type(sym, ref)
    assert verdict == "common", f"{sym} ({ref[sym]['name']}) was rejected: {reason}"


def test_unmarked_rights_line_is_caught_by_its_root(ref):
    """RDACR is a rights line whose name says only "Rising Dragon Acquisition
    Corp" -- no marker. It is caught by matching the root ticker RDAC, which
    carries the identical issuer name."""
    verdict, reason = instrument_type("RDACR", ref)
    assert verdict == "excluded"
    assert "RDAC" in reason


def test_root_comparison_does_not_fire_on_unrelated_roots(ref):
    """The comparison is what keeps the root rule safe. AMCR's root AMC is a
    different company, CR's root C is Citigroup, NVDA's root NVD is a 2x short
    fund. None share an issuer name, so none are excluded."""
    for sym, root in (("AMCR", "AMC"), ("CR", "C"), ("NVDA", "NVD")):
        if root in ref:
            assert ref[root]["name"] != ref[sym]["name"]
        assert instrument_type(sym, ref)[0] == "common"


def test_leverage_marker_beats_the_listing_type(ref):
    """SNXX is "Tradr 2X Long SNDK Daily" and `LISTING_STATUS` types it
    "Stock", not "ETF". Gating the leverage check on assetType let a 2x
    product through as common equity; the name is checked regardless."""
    assert ref["SNXX"]["assetType"] == "Stock"
    verdict, reason = instrument_type("SNXX", ref)
    assert verdict == "excluded", "a 2x product screened as common equity"
    assert "2X" in reason or "DAILY" in reason


def test_unknown_ticker_is_not_silently_admitted(ref):
    verdict, reason = instrument_type("ZZZZNOTREAL", ref)
    assert verdict == "unknown"
    assert reason


def test_majority_of_movers_are_excluded(ref):
    """Guards the finding that motivated the design: if a future change lets
    most of these through, the filter has stopped working."""
    verdicts = [instrument_type(s, ref)[0] for s in ref]
    excluded = sum(1 for v in verdicts if v != "common")
    assert excluded > len(verdicts) / 2, (
        f"only {excluded}/{len(verdicts)} excluded; the movers lists were "
        "majority derivatives when this was written"
    )


def test_words_matches_whole_tokens_only(ref):
    """Substring matching is what put Bitcoin in "DEX" via "Index" in the
    crypto worksheet. The equivalent here would be "GAMING" matching a video
    game studio into gambling."""
    assert "INDEX" in words("S&P 500 Index Fund")
    assert "DEX" not in words("S&P 500 Index Fund")
    assert words("Casino & Resorts, Inc.") == {"CASINO", "RESORTS", "INC"}


def test_every_flag_declares_why_it_fires():
    """A flag with no stated question is a verdict in disguise."""
    for code, keys, why in FLAGS:
        assert code and keys and why, f"{code} is missing its reason"
        assert all(k.isupper() for k in keys), f"{code} has non-upper keys"
