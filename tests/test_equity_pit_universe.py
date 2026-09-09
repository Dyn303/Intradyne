"""The point-in-time universe must keep the dead and must not merge companies.

Two properties carry the whole thing, and both are silent when wrong:

  A name that later delisted still belongs to every snapshot it was listed in.
  Drop it and every backtest over the period is flattered by exactly the names
  that did worst.

  A ticker is not an identity. 305 US symbols have been handed to an unrelated
  company after their first holder delisted, so keying by ticker splices two
  price series into one.

Neither shows up as a failure anywhere else -- a survivorship-biased universe
produces a *better-looking* result, not an error -- so they are pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from equity_pit_universe import (  # noqa: E402
    Listing,
    collisions,
    eligible,
    members_at,
    norm_name,
    rebalance_dates,
    timeline,
)


def L(symbol, ipo, delisted=None, name=None, exchange="NASDAQ", asset="Stock"):
    return Listing(
        symbol=symbol,
        name=name or f"{symbol} Inc",
        exchange=exchange,
        asset_type=asset,
        ipo=ipo,
        delisted=delisted,
    )


# ---- survivorship: the dead stay in ---------------------------------------


def test_a_delisted_name_is_a_member_before_it_died():
    dead = L("XYZ", "2010-01-01", "2015-06-30")
    assert dead.live_at("2012-01-01") is True


def test_a_delisted_name_is_not_a_member_after_it_died():
    dead = L("XYZ", "2010-01-01", "2015-06-30")
    assert dead.live_at("2016-01-01") is False


def test_the_universe_shrinks_when_a_name_dies_not_retroactively():
    """The point of A3 in one assertion: the 2012 snapshot is unaffected by a
    2015 delisting. A universe built from today's listings would drop XYZ from
    both."""
    names = [L("AAA", "2009-01-01"), L("XYZ", "2010-01-01", "2015-06-30")]
    assert [x.symbol for x in members_at(names, "2012-01-01")] == ["AAA", "XYZ"]
    assert [x.symbol for x in members_at(names, "2016-01-01")] == ["AAA"]


def test_delisting_date_is_exclusive():
    """A name that delisted on the rebalance date is not a member that day, but
    was on every earlier one."""
    dead = L("XYZ", "2010-01-01", "2015-06-30")
    assert dead.live_at("2015-06-29") is True
    assert dead.live_at("2015-06-30") is False


def test_ipo_date_is_inclusive():
    li = L("XYZ", "2010-01-01")
    assert li.live_at("2009-12-31") is False
    assert li.live_at("2010-01-01") is True


def test_a_still_listed_name_has_no_delisting_date():
    assert L("AAA", "2000-01-01").live_at("2026-01-01") is True


# ---- ticker reuse ---------------------------------------------------------


def test_two_companies_sharing_a_ticker_are_distinct_listings():
    old = L("ACCL", "1995-12-06", "2014-05-06", name="Accelrys Inc")
    new = L("ACCL", "2025-10-17", name="Acco Group Holdings Ltd")
    assert old.listing_id != new.listing_id
    assert old.listing_id == "ACCL@1995-12-06"


def test_each_era_of_a_reused_ticker_resolves_to_its_own_company():
    old = L("ACCL", "1995-12-06", "2014-05-06", name="Accelrys Inc")
    new = L("ACCL", "2025-10-17", name="Acco Group Holdings Ltd")
    both = [old, new]
    assert members_at(both, "2000-01-01")[0].name == "Accelrys Inc"
    assert members_at(both, "2026-01-01")[0].name == "Acco Group Holdings Ltd"
    # And in the gap the ticker belongs to nobody.
    assert members_at(both, "2020-01-01") == []


def test_a_symbol_is_never_returned_twice_on_one_date():
    """Concurrent listings exist -- shares and senior notes under one symbol --
    and a universe with the same symbol twice would double-weight it."""
    a = L("DUP", "2010-01-01", name="Dup Inc")
    b = L("DUP", "2012-01-01", name="Dup Inc 9pc Senior Notes")
    got = members_at([a, b], "2015-01-01")
    assert len(got) == 1
    assert got[0].ipo == "2012-01-01", "the later flotation should win"


def test_collisions_are_counted_not_hidden():
    a = L("DUP", "2010-01-01")
    b = L("DUP", "2012-01-01")
    c = collisions([a, b, L("AAA", "2000-01-01")], ["2015-01-01"])
    assert c["max"] == 1 and c["dates"] == 1


def test_no_collision_when_listings_are_sequential():
    a = L("SEQ", "2000-01-01", "2010-01-01")
    b = L("SEQ", "2012-01-01")
    assert collisions([a, b], ["2005-01-01", "2015-01-01"])["max"] == 0


# ---- name normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Absolute Software Corp", "Absolute Software Corporation"),
        ("Acme Inc", "Acme Incorporated"),
        ("Foo Holdings Ltd", "Foo Limited"),
        ("Bar Group PLC", "Bar"),
    ],
)
def test_a_re_registration_is_not_a_reassignment(a, b):
    assert norm_name(a) == norm_name(b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("ACap Energy Ltd", "Aurora Cannabis Inc"),
        ("Accelrys Inc", "Acco Group Holdings Ltd"),
        ("ADC Telecommunications Inc", "ADC Therapeutics SA"),
    ],
)
def test_distinct_companies_stay_distinct(a, b):
    assert norm_name(a) != norm_name(b)


# ---- filtering ------------------------------------------------------------


def test_etfs_are_excluded_by_default():
    rows = [L("AAA", "2000-01-01"), L("SPY", "1993-01-22", asset="ETF")]
    assert [x.symbol for x in eligible(rows, ["Stock"], ["NASDAQ"])] == ["AAA"]


def test_off_exchange_listings_are_excluded():
    rows = [L("AAA", "2000-01-01"), L("OTCX", "2000-01-01", exchange="OTC")]
    got = eligible(rows, ["Stock"], ["NASDAQ", "NYSE"])
    assert [x.symbol for x in got] == ["AAA"]


def test_exchange_match_is_case_insensitive():
    rows = [L("AAA", "2000-01-01", exchange="nasdaq")]
    assert len(eligible(rows, ["Stock"], ["NASDAQ"])) == 1


# ---- timeline -------------------------------------------------------------


def test_rebalance_dates_span_the_window():
    d = rebalance_dates("2020-01-01", "2020-12-31", 182)
    assert d[0] == "2020-01-01"
    assert all(a < b for a, b in zip(d, d[1:]))
    assert d[-1] <= "2020-12-31"


def test_timeline_reports_churn_not_just_size():
    rows = [
        L("AAA", "2000-01-01"),
        L("BBB", "2000-01-01", "2021-01-01"),
        L("CCC", "2021-06-01"),
    ]
    pts = timeline(rows, ["2020-01-01", "2022-01-01"])
    assert pts[0]["size"] == 2
    assert pts[1]["size"] == 2
    # One left and one arrived; a size-only view would show no change at all.
    assert pts[1]["removed"] == 1 and pts[1]["added"] == 1


def test_first_point_reports_no_churn():
    pts = timeline([L("AAA", "2000-01-01")], ["2020-01-01"])
    assert pts[0]["added"] == 0 and pts[0]["removed"] == 0
