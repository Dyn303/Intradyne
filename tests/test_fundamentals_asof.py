"""Fundamentals must be selected by publication date, not fiscal period end.

The defect: `screen_equities.balance_sheet` returned `reports[0]` -- the newest
report that exists. IBM's quarter ending 2025-12-31 was published 2026-01-28,
so a screen run in early January against `reports[0]` would have used figures
the market had not seen for another month.

The leak is silent and one-directional. It never raises, it makes results
better rather than worse, and it is largest for the names that report latest.
`test_a_report_is_not_used_before_it_was_published` is the one that fails on
the old code.

Dates here are IBM's real filing dates, from `EARNINGS.quarterlyEarnings`.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fundamentals_asof import (  # noqa: E402
    DEFAULT_LAG_DAYS,
    Filing,
    filings_from_earnings,
    latest_known,
    observed_lags,
    pick_report,
)

# Real IBM quarters: period end, publication date, session.
IBM = {
    "quarterlyEarnings": [
        {
            "fiscalDateEnding": "2025-12-31",
            "reportedDate": "2026-01-28",
            "reportTime": "post-market",
        },
        {
            "fiscalDateEnding": "2025-09-30",
            "reportedDate": "2025-10-22",
            "reportTime": "post-market",
        },
        {
            "fiscalDateEnding": "2025-06-30",
            "reportedDate": "2025-07-23",
            "reportTime": "post-market",
        },
    ]
}

REPORTS = [
    {"fiscalDateEnding": "2025-12-31", "shortLongTermDebtTotal": "300"},
    {"fiscalDateEnding": "2025-09-30", "shortLongTermDebtTotal": "200"},
    {"fiscalDateEnding": "2025-06-30", "shortLongTermDebtTotal": "100"},
]


def _filings():
    return filings_from_earnings(IBM)


# ---- the fix --------------------------------------------------------------


def test_a_report_is_not_used_before_it_was_published():
    """The regression. Old code took reports[0] -- the 2025-12-31 figures --
    on any date at all, including before they existed publicly."""
    got = pick_report(REPORTS, _filings(), date(2026, 1, 15))
    assert got is not None
    assert got["fiscalDateEnding"] == "2025-09-30", (
        "used a report published on 2026-01-28 while screening 2026-01-15"
    )


def test_the_newest_published_report_is_used_once_it_is_public():
    got = pick_report(REPORTS, _filings(), date(2026, 2, 1))
    assert got["fiscalDateEnding"] == "2025-12-31"


def test_nothing_is_returned_before_any_report_was_public():
    assert pick_report(REPORTS, _filings(), date(2025, 1, 1)) is None


# ---- the post-market boundary --------------------------------------------


def test_a_post_market_release_is_not_actionable_that_day():
    """IBM reported 2026-01-28 after the close. A trade on the 28th could not
    have used it; most releases in this data are post-market, so ignoring the
    field leaks a day on the majority of filings."""
    assert pick_report(REPORTS, _filings(), date(2026, 1, 28)) is not None
    assert (
        pick_report(REPORTS, _filings(), date(2026, 1, 28))["fiscalDateEnding"]
        == "2025-09-30"
    )
    assert (
        pick_report(REPORTS, _filings(), date(2026, 1, 29))["fiscalDateEnding"]
        == "2025-12-31"
    )


def test_a_pre_market_release_is_actionable_the_same_day():
    f = Filing(date(2025, 12, 31), date(2026, 1, 28), "pre-market")
    assert f.known_from() == date(2026, 1, 28)


def test_known_from_rolls_forward_only_for_post_market():
    post = Filing(date(2025, 12, 31), date(2026, 1, 28), "post-market")
    assert post.known_from() == date(2026, 1, 29)


# ---- missing publication dates fail late, not early ----------------------


def test_an_unknown_publication_date_falls_back_to_a_conservative_lag():
    """Guessing early would reintroduce the leak on exactly the names whose
    data is worst, so the fallback is deliberately pessimistic."""
    f = Filing(date(2025, 12, 31), None)
    assert f.known_from() == date(2025, 12, 31) + __import__("datetime").timedelta(
        days=DEFAULT_LAG_DAYS
    )


def test_the_fallback_is_later_than_a_real_filing_would_be():
    real = Filing(date(2025, 12, 31), date(2026, 1, 28), "post-market")
    unknown = Filing(date(2025, 12, 31), None)
    assert unknown.known_from() > real.known_from()


def test_a_report_with_no_matching_filing_is_not_silently_dropped():
    """Dropping it would shrink the universe on the thinnest-data names."""
    reports = [{"fiscalDateEnding": "2024-03-31", "shortLongTermDebtTotal": "50"}]
    assert pick_report(reports, _filings(), date(2024, 3, 31)) is None
    got = pick_report(reports, _filings(), date(2025, 1, 1))
    assert got is not None and got["fiscalDateEnding"] == "2024-03-31"


def test_default_lag_is_the_sec_non_accelerated_deadline():
    assert DEFAULT_LAG_DAYS == 90


# ---- provenance travels with the figures ---------------------------------


def test_the_chosen_report_carries_its_own_dates():
    got = pick_report(REPORTS, _filings(), date(2026, 2, 1))
    assert got["_known_from"] == "2026-01-29"
    assert got["_as_of"] == "2026-02-01"
    assert got["fiscalDateEnding"] == "2025-12-31"
    # Three distinct dates: a ratio cannot be audited from any one of them.
    assert len({got["fiscalDateEnding"], got["_known_from"], got["_as_of"]}) == 3


def test_picking_does_not_mutate_the_source_report():
    before = dict(REPORTS[0])
    pick_report(REPORTS, _filings(), date(2026, 2, 1))
    assert REPORTS[0] == before


# ---- parsing --------------------------------------------------------------


def test_filings_parse_in_chronological_order():
    f = _filings()
    assert [x.fiscal_end.isoformat() for x in f] == [
        "2025-06-30",
        "2025-09-30",
        "2025-12-31",
    ]


@pytest.mark.parametrize("payload", [None, {}, {"quarterlyEarnings": []}, "nope"])
def test_missing_earnings_parses_to_nothing_rather_than_raising(payload):
    assert filings_from_earnings(payload) == []


def test_a_row_without_a_period_end_is_skipped():
    got = filings_from_earnings({"quarterlyEarnings": [{"reportedDate": "2026-01-28"}]})
    assert got == []


def test_an_unparseable_reported_date_becomes_the_fallback():
    got = filings_from_earnings(
        {
            "quarterlyEarnings": [
                {"fiscalDateEnding": "2025-12-31", "reportedDate": "whenever"}
            ]
        }
    )
    assert got[0].reported is None
    assert got[0].lag_days() is None


# ---- latest_known ---------------------------------------------------------


def test_latest_known_picks_the_most_recent_public_period():
    f = latest_known(_filings(), date(2025, 11, 1))
    assert f.fiscal_end == date(2025, 9, 30)


def test_latest_known_returns_none_when_nothing_is_public():
    assert latest_known(_filings(), date(2024, 1, 1)) is None


# ---- the fallback checked against evidence -------------------------------


def test_observed_lags_show_the_fallback_is_pessimistic():
    """The fallback should be later than real filings, not calibrated to them.
    IBM's actual lags are around three to four weeks."""
    stats = observed_lags(_filings())
    assert stats["n"] == 3
    assert stats["max"] < DEFAULT_LAG_DAYS
    assert 20 <= stats["median"] <= 35


def test_observed_lags_on_an_empty_sample():
    assert observed_lags([])["n"] == 0
