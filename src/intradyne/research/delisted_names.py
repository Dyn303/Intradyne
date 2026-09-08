"""Recover tickers for acquired companies, from the delisted listing record.

`sec_names` recovers live companies OpenFIGI fails to index. It cannot recover
the ones that matter most here, and says so: Abiomed and Twitter are not SEC
registrants any more, so `company_tickers.json` has no row for them. That left
P3 -- coverage of names that *left* the SPUS universe -- at 75.8% against an
80% floor, and the conclusion recorded at the time was that only a paid
security master could close it.

That conclusion was wrong. `docs/equity_listings.csv`, already committed by
`scripts/equity_pit_universe.py`, carries every US listing ever seen including
7,473 dead ones, each with its issuer name and delisting date. Matching N-PORT
names against the dead half recovers exactly the population SEC drops.

Alpha Vantage then serves those tickers' price history on the free tier:
`TIME_SERIES_DAILY` for `ABMD` returns bars through 2023-01-03, its delisting
date, including the takeover premium. So the tail is reachable without a
purchase.

## Exact matching only, for the reason `sec_names` gives

Normalisation and comparison are imported from `sec_names` rather than
reimplemented, so both fallbacks share one definition of what makes two names
the same company. A second normaliser would drift from the first, and the two
would disagree about a name on the boundary without anyone noticing.

A fuzzy match always returns something, which is how `cusip_map` once mapped
Cencora to Asbury Automotive. A name that does not match exactly resolves to
nothing.

## The guard this needs and `sec_names` does not

SEC's registrant list is a snapshot of the living, so a match there is
unambiguous in time. The delisted record spans decades, and ticker names are
reused: matching on name alone would happily bind a 2021 holding to an
identically-named company that died in 2003.

So a candidate listing must have been **alive while the fund actually held
it** -- its listing interval must overlap the holding window. `ADCT` was ADC
Telecommunications until 2010 and is ADC Therapeutics now; the overlap test is
what keeps those apart, and it is the same (symbol, ipoDate) listing identity
`equity_pit_universe` established for the same reason.

A name whose surviving candidates name more than one symbol is dropped rather
than arbitrated, exactly as in `sec_names.load_registry`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from .sec_names import normalise

#: Written by `scripts/equity_pit_universe.py`, committed so this resolves on a
#: fresh clone with no API key. Already filtered to common stock on real
#: exchanges -- SPY is absent -- so a fund cannot be matched to a holding.
LISTINGS = Path("docs/equity_listings.csv")

#: Anything but a plain ticker. Alpha Vantage marks non-common lines with a
#: separator and a form code: `-P-` preferred, `-U` unit, `-WS`/`-W` warrant,
#: `-R` right, `-CL` called, `-A`/`-B` share class.
#:
#: All of them are rejected, and the reason is a match this found before the
#: filter existed: Aptiv's common is still listed, so the only dead listing
#: carrying the name was `APTV-P-A`, its mandatory convertible preferred, and
#: the holding window did not separate them -- the preferred was alive
#: throughout. A preferred line's price history is not the common's, and
#: writing it into the panel is precisely the silent issuer substitution this
#: module exists to prevent.
#:
#: Share classes are rejected too, though `-A` common really is common. Only
#: 12 dead listings carry a class suffix, and the name alone cannot say which
#: class a CUSIP denotes, so keeping them would trade a negligible recovery
#: for a real chance of binding Class A prices to a Class C holding.
#:
#: The separator is what makes this safe. A bare ticker ending in W or R --
#: `LOW`, `AMCR`, `BKR` -- has no separator and is untouched, which is the
#: over-rejection the suffix heuristic in `screen_equities.py` has to guard
#: against and this one gets for free.
_NOT_COMMON = re.compile(r"[-+.$]")


@dataclass(frozen=True)
class Listing:
    """One dead listing: a (symbol, ipo) interval, not a ticker.

    `symbol` is a label on the interval rather than an identity, because the
    exchanges reissue tickers. `delisted` is always present here; live
    listings are `sec_names`' problem and are excluded on load.
    """

    symbol: str
    name: str
    exchange: str
    ipo: str
    delisted: str

    def overlaps(self, first_held: str, last_held: str) -> bool:
        """True when this listing was tradeable at some point in the window.

        Dates are ISO strings, so lexicographic comparison is chronological.
        The delisting bound is exclusive to match `Listing.live_at` in
        `scripts/equity_pit_universe.py`: a name that delisted on a date did
        not trade on it.
        """
        return self.ipo <= last_held and self.delisted > first_held


def load_delisted(path: Path = LISTINGS) -> Dict[str, List[Listing]]:
    """Normalised issuer name to the dead listings carrying it.

    Only plain common-stock symbols are carried; see `_NOT_COMMON`.

    Unlike `sec_names.load_registry` this keeps every candidate rather than
    dropping ambiguous keys on load. Ambiguity here is often resolved by the
    holding window -- two companies shared a name across different decades --
    so discarding it now would throw away recoverable names. It is resolved in
    `recover`, after the time guard has run.
    """
    out: Dict[str, List[Listing]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            delisted = (row.get("delisted") or "").strip()
            if not delisted:
                continue
            symbol = (row.get("symbol") or "").strip()
            if not symbol or _NOT_COMMON.search(symbol):
                continue
            key = normalise(row.get("name"))
            if not key:
                continue
            out.setdefault(key, []).append(
                Listing(
                    symbol=symbol,
                    name=(row.get("name") or "").strip(),
                    exchange=(row.get("exchange") or "").strip(),
                    ipo=(row.get("ipo") or "").strip(),
                    delisted=delisted,
                )
            )
    return out


def by_symbol(path: Path = LISTINGS) -> Dict[str, List[Listing]]:
    """Dead listings indexed by ticker, for routing rather than matching.

    The cascade resolves most names through OpenFIGI and SEC, which say what a
    CUSIP's ticker is and nothing about whether it still trades. Routing those
    to a live-only provider is what produced the 53.1% tail coverage in the
    first place -- yfinance answers a dead ticker with an empty frame that
    reads as a network failure.

    So liveness is looked up here, by symbol, independently of how the ticker
    was resolved.
    """
    out: Dict[str, List[Listing]] = {}
    for listings in load_delisted(path).values():
        for li in listings:
            out.setdefault(li.symbol, []).append(li)
    return out


def recover(
    unresolved_names: Mapping[str, Optional[str]],
    registry: Mapping[str, List[Listing]],
    held: Mapping[str, Tuple[str, str]],
) -> Dict[str, Listing]:
    """CUSIP to dead listing, for names that match exactly and line up in time.

    `unresolved_names` maps CUSIP to the name N-PORT recorded. `held` maps
    CUSIP to its (first, last) holding date, which is what makes the match
    checkable against the calendar rather than only against a string.

    A CUSIP with no holding window is skipped rather than matched loosely: the
    window is the guard, and a match made without it is the failure mode this
    module exists to avoid.
    """
    out: Dict[str, Listing] = {}
    for cusip, name in unresolved_names.items():
        key = normalise(name)
        window = held.get(cusip)
        if not key or window is None:
            continue
        candidates = [li for li in registry.get(key, ()) if li.overlaps(*window)]
        if not candidates:
            continue
        if len({li.symbol for li in candidates}) != 1:
            continue
        out[cusip] = candidates[0]
    return out


__all__ = ["LISTINGS", "Listing", "load_delisted", "recover"]
