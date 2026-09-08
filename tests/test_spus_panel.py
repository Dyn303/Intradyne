"""The SPUS timeline to Panel bridge, and the two mistakes it exists to prevent.

N-PORT is filed 54-60 days after the period it describes. Dating membership to
the as-of date applies a two-month look-ahead to every rebalance, and it
flatters results in exactly the direction that makes a strategy look tradeable.

And a name that was never held is different from a name that was held and not
priced: the first is information, the second is a data gap that
`SLOT_1_PREREGISTRATION.md` aborts on.
"""

from __future__ import annotations

import datetime as dt
import json
from datetime import date
from typing import Dict, Mapping, Optional, Set

import numpy as np
import pytest

from intradyne.research.spus_panel import (
    FROM_AS_OF,
    FROM_FILED,
    build_panel,
    describe,
    membership_windows,
)


def _timeline(tmp_path, entries):
    doc = {}
    for as_of, filed, cusips in entries:
        doc[as_of] = {
            "filed": filed,
            "accession": "x",
            "n": len(cusips),
            "holdings": [
                {"cusip": c, "name": c, "pct": 1.0, "ticker": None} for c in cusips
            ],
        }
    p = tmp_path / "timeline.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


class FlatPrices:
    """Every CUSIP priced at a constant on every weekday."""

    def __init__(self, known: Optional[Set[str]] = None, level: float = 100.0) -> None:
        self.known = known
        self.level = level

    def close_series(self, cusip: str, start: date, end: date) -> Mapping[date, float]:
        if self.known is not None and cusip not in self.known:
            return {}
        out: Dict[date, float] = {}
        d = start
        while d <= end:
            if d.weekday() < 5:
                out[d] = self.level
            d += dt.timedelta(days=1)
        return out


class TestLookAhead:
    """The mistake that matters most."""

    def test_membership_starts_at_the_filing_date_by_default(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA"])])
        panel, _ = build_panel(
            FlatPrices(), tl, start=date(2020, 5, 1), end=date(2020, 8, 31)
        )
        live = [d for d, m in zip(panel.dates, panel.membership[:, 0]) if m]
        assert live, "no membership at all"
        first = dt.datetime.fromtimestamp(min(live), dt.timezone.utc).date()
        assert first >= date(2020, 7, 28), (
            f"membership began {first}, before the filing existed -- a look-ahead"
        )

    def test_as_of_mode_is_available_but_earlier(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA"])])
        a, _ = build_panel(
            FlatPrices(),
            tl,
            effective=FROM_AS_OF,
            start=date(2020, 5, 1),
            end=date(2020, 8, 31),
        )
        f, _ = build_panel(
            FlatPrices(),
            tl,
            effective=FROM_FILED,
            start=date(2020, 5, 1),
            end=date(2020, 8, 31),
        )
        assert a.membership.sum() > f.membership.sum(), (
            "as-of mode must admit more sessions, or the lag is not applied"
        )

    def test_an_unknown_mode_is_refused(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA"])])
        with pytest.raises(ValueError, match="effective must be"):
            build_panel(FlatPrices(), tl, effective="whenever")


class TestWindows:
    def test_a_quarter_holds_until_the_next_supersedes_it(self, tmp_path):
        tl = _timeline(
            tmp_path,
            [
                ("2020-05-31", "2020-07-28", ["AAA"]),
                ("2020-08-31", "2020-10-28", ["BBB"]),
            ],
        )
        w = membership_windows(json.loads(tl.read_text()), FROM_FILED)
        assert [x[0] for x in w] == [date(2020, 7, 28), date(2020, 10, 28)]
        assert w[0][1] == date(2020, 10, 28), "window must end where the next starts"
        assert w[1][1] is None, "the last window runs to the end of the panel"

    def test_windows_sort_by_effective_date_not_period(self, tmp_path):
        """Ordering by as-of and by filing can differ; the sort must follow
        whichever date is actually in force."""
        tl = _timeline(
            tmp_path,
            [
                ("2020-08-31", "2020-09-01", ["BBB"]),
                ("2020-05-31", "2020-11-01", ["AAA"]),  # older period, filed later
            ],
        )
        w = membership_windows(json.loads(tl.read_text()), FROM_FILED)
        assert w[0][2] == ["BBB"], "windows are not ordered by effective date"

    def test_a_holding_with_no_cusip_is_dropped_not_crashed_on(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(
            json.dumps(
                {
                    "2020-05-31": {
                        "filed": "2020-07-28",
                        "holdings": [
                            {"cusip": None, "name": "x", "pct": 1.0},
                            {"cusip": "AAA", "name": "y", "pct": 1.0},
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        w = membership_windows(json.loads(p.read_text()), FROM_FILED)
        assert w[0][2] == ["AAA"]


class TestCoverage:
    """Not-held and not-priced are different facts."""

    def test_an_unpriced_member_is_counted_against_coverage(self, tmp_path):
        tl = _timeline(
            tmp_path, [("2020-05-31", "2020-07-28", ["AAA", "BBB", "CCC", "DDD"])]
        )
        _, cov = build_panel(
            FlatPrices(known={"AAA", "BBB"}),
            tl,
            start=date(2020, 7, 1),
            end=date(2020, 9, 30),
        )
        assert cov.by_quarter["2020-05-31"] == (2, 4)
        assert cov.below(0.80) == ["2020-05-31"]

    def test_full_coverage_passes_the_floor(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA", "BBB"])])
        _, cov = build_panel(
            FlatPrices(), tl, start=date(2020, 7, 1), end=date(2020, 9, 30)
        )
        assert cov.below(0.80) == []
        assert cov.worst()[1] == pytest.approx(1.0)

    def test_the_description_states_the_abort(self, tmp_path):
        tl = _timeline(
            tmp_path, [("2020-05-31", "2020-07-28", ["AAA", "BBB", "CCC", "DDD"])]
        )
        panel, cov = build_panel(
            FlatPrices(known={"AAA"}),
            tl,
            start=date(2020, 7, 1),
            end=date(2020, 9, 30),
        )
        text = describe(panel, cov)
        assert "BELOW 80%" in text
        assert "not spent" in text

    def test_a_source_that_raises_does_not_take_the_panel_down(self, tmp_path):
        class Broken(FlatPrices):
            def close_series(self, cusip, start, end):
                if cusip == "BBB":
                    raise RuntimeError("cannot resolve")
                return super().close_series(cusip, start, end)

        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA", "BBB"])])
        _, cov = build_panel(
            Broken(), tl, start=date(2020, 7, 1), end=date(2020, 9, 30)
        )
        assert cov.by_quarter["2020-05-31"] == (1, 2)


class TestPanelShape:
    def test_it_produces_a_panel_the_harness_accepts(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA", "BBB"])])
        panel, _ = build_panel(
            FlatPrices(), tl, start=date(2020, 7, 1), end=date(2020, 9, 30)
        )
        t, n = len(panel.dates), len(panel.symbols)
        assert panel.close.shape == (t, n)
        assert panel.membership.shape == (t, n)
        assert np.isfinite(panel.close[panel.membership]).all()

    def test_weekends_are_excluded(self, tmp_path):
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA"])])
        panel, _ = build_panel(
            FlatPrices(), tl, start=date(2020, 7, 1), end=date(2020, 7, 31)
        )
        days = [
            dt.datetime.fromtimestamp(x, dt.timezone.utc).date().weekday()
            for x in panel.dates
        ]
        assert max(days) < 5

    def test_a_missing_timeline_is_a_clear_error(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="spus_universe.py"):
            build_panel(FlatPrices(), tmp_path / "nope.json")

    def test_an_empty_timeline_is_refused(self, tmp_path):
        p = tmp_path / "e.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="empty or malformed"):
            build_panel(FlatPrices(), p)


class TestDroppedCoverage:
    """Precondition P3, as amended.

    Overall coverage is dominated by survivors and cannot fail for the reason
    that matters. Measured on the free data path: names still held were 98.0%
    priceable, names that had left were 53.1%, and 44 of the 48 gaps sat in
    the tail. A panel can report 85% coverage and be survivorship-biased in
    the same way a today's-holdings list is.
    """

    def _two_quarters(self, tmp_path):
        # AAA leaves the fund after the first quarter; BBB stays.
        return _timeline(
            tmp_path,
            [
                ("2020-05-31", "2020-07-28", ["AAA", "BBB"]),
                ("2020-08-31", "2020-10-28", ["BBB"]),
            ],
        )

    def test_a_dropped_name_with_no_price_fails_p3(self, tmp_path):
        tl = self._two_quarters(tmp_path)
        _, cov = build_panel(
            FlatPrices(known={"BBB"}),
            tl,
            start=date(2020, 7, 1),
            end=date(2021, 1, 31),
        )
        assert cov.dropped_total == 1
        assert cov.dropped_coverage() == 0.0

    def test_a_priced_dropped_name_passes(self, tmp_path):
        tl = self._two_quarters(tmp_path)
        _, cov = build_panel(
            FlatPrices(), tl, start=date(2020, 7, 1), end=date(2021, 1, 31)
        )
        assert cov.dropped_coverage() == pytest.approx(1.0)

    def test_overall_coverage_can_pass_while_p3_fails(self, tmp_path):
        """The whole reason for the amendment: a survivor-heavy universe keeps
        overall coverage high while the tail is empty."""
        tl = _timeline(
            tmp_path,
            [
                ("2020-05-31", "2020-07-28", ["GONE", "S1", "S2", "S3", "S4"]),
                ("2020-08-31", "2020-10-28", ["S1", "S2", "S3", "S4"]),
            ],
        )
        _, cov = build_panel(
            FlatPrices(known={"S1", "S2", "S3", "S4"}),
            tl,
            start=date(2020, 7, 1),
            end=date(2021, 1, 31),
        )
        assert cov.below(0.80) == [], "overall coverage passes"
        assert cov.dropped_coverage() == 0.0, "yet the tail is empty"

    def test_the_description_states_the_p3_failure(self, tmp_path):
        tl = self._two_quarters(tmp_path)
        panel, cov = build_panel(
            FlatPrices(known={"BBB"}),
            tl,
            start=date(2020, 7, 1),
            end=date(2021, 1, 31),
        )
        text = describe(panel, cov)
        assert "P3 FAILS" in text
        assert "dominated by" in text
        assert "Slot not spent" in text

    def test_no_dropped_names_is_not_a_failure(self, tmp_path):
        """A universe nobody has left yet has an empty tail, which is not the
        same as an uncovered one."""
        tl = _timeline(tmp_path, [("2020-05-31", "2020-07-28", ["AAA"])])
        panel, cov = build_panel(
            FlatPrices(), tl, start=date(2020, 7, 1), end=date(2020, 9, 30)
        )
        assert cov.dropped_total == 0
        assert "P3 FAILS" not in describe(panel, cov)
