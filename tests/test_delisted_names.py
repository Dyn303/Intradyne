"""Traps the delisted-name recovery has to keep passing.

Every test here corresponds to a wrong answer this code produced, or would
have produced, before the guard it exercises existed. The instrument-form and
time-guard cases are the two that matter: both substitute a different security
silently, which is the failure mode a resolver is most likely to hide.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from intradyne.research.delisted_names import (
    LISTINGS,
    Listing,
    load_delisted,
    recover,
)
from intradyne.research.price_source import (
    CachedPrices,
    Resolution,
    window_coverage,
)

HEADER = ["listing_id", "symbol", "name", "exchange", "ipo", "delisted"]


def _listings(tmp_path: Path, rows) -> Path:
    p = tmp_path / "listings.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r)
    return p


# -- instrument form ----------------------------------------------------


def test_preferred_line_is_not_matched_to_the_common(tmp_path: Path) -> None:
    """The Aptiv case, which this found before the filter existed.

    Aptiv's common is still listed, so the only *dead* listing carrying the
    name was its mandatory convertible preferred. The holding window does not
    separate them -- the preferred was alive throughout -- so without a form
    filter the preferred's price history enters the panel as the common's.
    """
    path = _listings(
        tmp_path,
        [
            [
                "APTV-P-A@2020-06-01",
                "APTV-P-A",
                "Aptiv PLC",
                "NYSE",
                "2020-06-01",
                "2023-06-14",
            ]
        ],
    )
    assert load_delisted(path) == {}


@pytest.mark.parametrize(
    "symbol", ["FOO-U", "FOO-WS", "FOO-W", "FOO-R", "FOO-CL", "FOO-A", "FOO-P-B"]
)
def test_every_non_common_form_is_refused(tmp_path: Path, symbol: str) -> None:
    path = _listings(
        tmp_path,
        [
            [
                f"{symbol}@2000-01-01",
                symbol,
                "Foo Inc",
                "NYSE",
                "2000-01-01",
                "2010-01-01",
            ]
        ],
    )
    assert load_delisted(path) == {}


@pytest.mark.parametrize("symbol", ["LOW", "AMCR", "BKR", "CTLT"])
def test_plain_tickers_ending_in_form_letters_survive(
    tmp_path: Path, symbol: str
) -> None:
    """The over-rejection a bare suffix heuristic makes and this one does not.

    `LOW`, `AMCR` and `BKR` end in W, R and R. They are common stock, and the
    separator is what distinguishes them from `FOO-W`.
    """
    path = _listings(
        tmp_path,
        [
            [
                f"{symbol}@2000-01-01",
                symbol,
                "Foo Inc",
                "NYSE",
                "2000-01-01",
                "2010-01-01",
            ]
        ],
    )
    assert list(load_delisted(path)) == ["FOO"]


def test_live_listings_are_not_carried(tmp_path: Path) -> None:
    """A live name is `sec_names`' job; carrying it here would double-answer."""
    path = _listings(
        tmp_path, [["AAPL@1980-12-12", "AAPL", "Apple Inc", "NASDAQ", "1980-12-12", ""]]
    )
    assert load_delisted(path) == {}


# -- the time guard -----------------------------------------------------


def test_name_reused_by_a_later_company_is_not_matched_backwards() -> None:
    """The ticker-reuse trap, in the direction that matters.

    `ADCT` was ADC Telecommunications until 2010 and is ADC Therapeutics now.
    A fund holding the second must never be bound to the first's prices, and
    name matching alone cannot tell them apart.
    """
    old = Listing("ADCT", "ADC Inc", "NASDAQ", "1991-01-01", "2010-12-01")
    registry = {"ADC": [old]}
    held = {"CUSIP1": ("2020-05-31", "2022-05-31")}
    assert recover({"CUSIP1": "ADC Inc"}, registry, held) == {}


def test_overlapping_listing_is_matched() -> None:
    dead = Listing("ABMD", "ABIOMED Inc", "NASDAQ", "1987-07-30", "2023-01-03")
    got = recover(
        {"003654100": "ABIOMED Inc"},
        {"ABIOMED": [dead]},
        {"003654100": ("2020-05-31", "2022-11-30")},
    )
    assert got["003654100"].symbol == "ABMD"


def test_delisting_bound_is_exclusive() -> None:
    """A name that delisted on a date did not trade on it.

    Matches `Listing.live_at` in `scripts/equity_pit_universe.py`; the two
    must agree or membership and pricing disagree about the final session.
    """
    li = Listing("X", "Foo Inc", "NYSE", "2000-01-01", "2020-05-31")
    assert not li.overlaps("2020-05-31", "2021-01-01")
    assert li.overlaps("2020-05-30", "2021-01-01")


def test_a_cusip_with_no_holding_window_is_skipped() -> None:
    """The window is the guard; a match made without it is unguarded."""
    li = Listing("X", "Foo Inc", "NYSE", "2000-01-01", "2020-01-01")
    assert recover({"C": "Foo Inc"}, {"FOO": [li]}, {}) == {}


def test_two_companies_sharing_a_name_in_window_are_dropped() -> None:
    a = Listing("AAA", "Foo Inc", "NYSE", "2000-01-01", "2021-01-01")
    b = Listing("BBB", "Foo Corp", "NASDAQ", "2000-01-01", "2021-01-01")
    got = recover(
        {"C": "Foo Inc"}, {"FOO": [a, b]}, {"C": ("2020-01-01", "2020-06-01")}
    )
    assert got == {}


def test_unmatched_name_resolves_to_nothing_rather_than_the_nearest() -> None:
    """Exact matching only. A fuzzy matcher always returns something."""
    li = Listing("LH", "Labcorp Holdings Inc", "NYSE", "1990-01-01", "2020-01-01")
    got = recover(
        {"C": "Laboratory Corp of America Hol"},
        {"LABCORP": [li]},
        {"C": ("2019-01-01", "2019-06-01")},
    )
    assert got == {}


# -- against the committed record ---------------------------------------


def test_known_acquisitions_resolve_from_the_committed_listings() -> None:
    """The names that failed P3, resolved with no network call."""
    if not LISTINGS.exists():  # pragma: no cover - depends on checkout
        pytest.skip(f"{LISTINGS} not present")
    registry = load_delisted()
    window = {c: ("2020-05-31", "2021-05-31") for c in ("a", "b", "c")}
    got = recover(
        {"a": "ABIOMED Inc", "b": "Twitter Inc", "c": "Activision Blizzard Inc"},
        registry,
        window,
    )
    assert {c: li.symbol for c, li in got.items()} == {
        "a": "ABMD",
        "b": "TWTR",
        "c": "ATVI",
    }
    assert got["a"].delisted == "2023-01-03"


# -- price source -------------------------------------------------------


class _Stub(CachedPrices):
    """CachedPrices with the network replaced, so the logic is testable."""

    def __init__(self, tmp_path: Path, series, splits, **kw):
        super().__init__({}, cache_dir=tmp_path, **kw)
        self._series = series
        self._splits = splits

    def _av(self, params, tries=3):
        if params["function"] == "SPLITS":
            return self._splits
        return self._series


def test_split_inside_the_window_is_back_adjusted(tmp_path: Path) -> None:
    """Without this a 2:1 split reads as a -50% return."""
    series = (
        "timestamp,open,high,low,close,volume\n"
        "2021-01-04,1,1,1,50,1\n"
        "2020-12-31,1,1,1,100,1\n"
        "2020-01-02,1,1,1,90,1\n"
    )
    splits = "effective_date,split_factor\n2021-01-01,2.0\n"
    p = _Stub(tmp_path, series, splits)
    got = p._fetch_av_daily("FOO")
    assert got[date(2021, 1, 4)] == pytest.approx(50.0)
    assert got[date(2020, 12, 31)] == pytest.approx(50.0)
    assert got[date(2020, 1, 2)] == pytest.approx(45.0)
    assert p.splits_applied == {"FOO": 1}


def test_split_outside_the_window_is_not_applied(tmp_path: Path) -> None:
    """ABMD's only split was in 2000; the 2020s series must be untouched."""
    series = (
        "timestamp,open,high,low,close,volume\n"
        "2022-12-21,1,1,1,381,1\n"
        "2022-08-04,1,1,1,292,1\n"
    )
    splits = "effective_date,split_factor\n2000-10-02,2.0\n"
    p = _Stub(tmp_path, series, splits)
    got = p._fetch_av_daily("ABMD")
    assert got[date(2022, 12, 21)] == pytest.approx(381.0)
    assert p.splits_applied == {}


def test_unavailable_split_history_withholds_the_series(tmp_path: Path) -> None:
    """Unknown is not none.

    Counting a name as priced when its split history could not be read would
    let an unadjusted split into the panel as a fabricated return, and P3
    would report the name as covered.
    """
    series = "timestamp,open,high,low,close,volume\n2022-12-21,1,1,1,381,1\n"
    p = _Stub(tmp_path, series, None)
    assert p._fetch_av_daily("FOO") is None
    assert "FOO" in p.failures


def test_close_series_is_clipped_to_the_requested_window(tmp_path: Path) -> None:
    src = CachedPrices(
        {"C": Resolution("FOO", delisted="2022-01-01")}, cache_dir=tmp_path
    )
    src._store(
        "FOO", {date(2020, 1, 1): 1.0, date(2021, 6, 1): 2.0, date(2022, 1, 1): 3.0}
    )
    got = src.close_series("C", date(2021, 1, 1), date(2021, 12, 31))
    assert got == {date(2021, 6, 1): 2.0}


def test_unresolved_cusip_prices_to_nothing(tmp_path: Path) -> None:
    src = CachedPrices({}, cache_dir=tmp_path)
    assert src.close_series("missing", date(2020, 1, 1), date(2021, 1, 1)) == {}


def test_empty_cache_file_is_honoured_rather_than_refetched(tmp_path: Path) -> None:
    """A known absence must not be rediscovered; the quota is 25 a day."""
    src = CachedPrices(
        {"C": Resolution("FOO", delisted="2022-01-01")}, cache_dir=tmp_path
    )
    src._store("FOO", {})
    assert src.series_for("FOO", delisted=True) == {}
    assert src.requests_made == 0


def test_a_failed_request_is_not_cached_as_an_absence(tmp_path: Path) -> None:
    """The bug that made a wrong parameter permanent.

    A first run asked for `outputsize=full`, which the free tier refuses, and
    every delisted name came back empty. Because failures were cached like
    absences, all 21 were written as empty files -- so every later run would
    have honoured them and reported the same names unpriceable without
    spending one request to find out otherwise.
    """
    p = _Stub(tmp_path, None, None)  # provider refuses the series
    assert p.series_for("FOO", delisted=True) == {}
    assert not (tmp_path / "FOO.csv").exists()


def test_a_genuine_absence_is_cached(tmp_path: Path) -> None:
    """The other half: a real "nothing here" must not be re-asked daily."""
    p = _Stub(tmp_path, "timestamp,open,high,low,close,volume\n", "")
    assert p.series_for("FOO", delisted=True) == {}
    assert (tmp_path / "FOO.csv").exists()


def test_window_coverage_counts_weekdays_not_calendar_days() -> None:
    # Mon 2021-01-04 .. Fri 2021-01-08 is five sessions.
    series = {date(2021, 1, d): 1.0 for d in (4, 5, 6, 7, 8)}
    assert window_coverage(series, date(2021, 1, 4), date(2021, 1, 10)) == 1.0


def test_partial_history_does_not_count_as_priced() -> None:
    """100 sessions against a multi-year window is not coverage.

    On the free tier a delisted name returns its last 100 sessions, which
    overlaps the SPUS holding windows by a median of 5.5%. Counting overlap
    would pass P3 at 85% on series covering a twentieth of their period.
    """
    series = {
        date(2022, 12, d): 1.0 for d in range(1, 22) if date(2022, 12, d).weekday() < 5
    }
    cov = window_coverage(series, date(2020, 5, 31), date(2022, 12, 31))
    assert cov < 0.05


def test_window_coverage_is_zero_for_a_series_outside_the_window() -> None:
    series = {date(2024, 6, 3): 1.0}
    assert window_coverage(series, date(2020, 1, 1), date(2021, 1, 1)) == 0.0
