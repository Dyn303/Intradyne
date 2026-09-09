"""CUSIP to ticker resolution, and the substitution it nearly made silently.

OpenFIGI answers a CUSIP with every venue that lists the security -- HP Inc
returns 172 rows, the German lines first. Taking the first row gave `7HP` for
HP, `8CW` for Crown Castle, `ABMDEUR` for Abiomed, and `ABG` for Cencora --
and ABG is Asbury Automotive, a different company. Fed to a price source those
return Frankfurt prices in EUR or another issuer's history, and nothing
downstream notices.
"""

from __future__ import annotations

import json

import pytest

from intradyne.research.cusip_map import (
    Mapping_,
    _pick_us_listing,
    resolve,
    summarise,
)


def _row(ticker, exch, sec="Common Stock", sector="Equity"):
    return {
        "ticker": ticker,
        "exchCode": exch,
        "securityType": sec,
        "marketSector": sector,
        "name": f"{ticker} CORP",
    }


class TestListingSelection:
    """The bug that mattered."""

    def test_the_us_composite_wins_over_foreign_lines(self):
        rows = [_row("7HP", "GR"), _row("7HP", "GF"), _row("HPQ", "US")]
        assert _pick_us_listing(rows)["ticker"] == "HPQ"

    def test_order_does_not_decide(self):
        """The German rows come first in the real response; that must not
        matter."""
        a = _pick_us_listing([_row("HPQ", "US"), _row("7HP", "GR")])
        b = _pick_us_listing([_row("7HP", "GR"), _row("HPQ", "US")])
        assert a["ticker"] == b["ticker"] == "HPQ"

    def test_a_security_with_no_us_listing_resolves_to_nothing(self):
        """A true answer, rather than a foreign substitute. Abiomed was
        acquired in 2022 and has no US line; pricing it off Frankfurt would be
        worse than admitting the gap."""
        assert _pick_us_listing([_row("ABMDEUR", "GR"), _row("ABMD", "GY")]) == {}

    def test_non_equity_rows_are_ignored(self):
        rows = [_row("XYZ", "US", sector="Corp"), _row("ABC", "US")]
        assert _pick_us_listing(rows)["ticker"] == "ABC"

    def test_a_row_without_a_ticker_is_ignored(self):
        rows = [_row(None, "US"), _row("ABC", "US")]
        assert _pick_us_listing(rows)["ticker"] == "ABC"

    def test_common_stock_is_preferred_within_us(self):
        rows = [_row("ABCp", "US", sec="Preferred"), _row("ABC", "US")]
        assert _pick_us_listing(rows)["ticker"] == "ABC"

    def test_an_empty_response_is_empty(self):
        assert _pick_us_listing([]) == {}

    def test_malformed_rows_do_not_raise(self):
        assert _pick_us_listing([None, "nonsense", {}, 7]) == {}


class TestCache:
    def test_a_cached_mapping_is_not_requeried(self, tmp_path, monkeypatch):
        cache = tmp_path / "m.json"
        cache.write_text(
            json.dumps({"ABC": {"ticker": "ABC", "name": "A", "exchange": "US"}}),
            encoding="utf-8",
        )

        def explode(*a, **k):  # pragma: no cover - must not run
            raise AssertionError("network hit for a cached CUSIP")

        monkeypatch.setattr("intradyne.research.cusip_map._post", explode)
        out = resolve(["ABC"], cache_path=cache)
        assert out["ABC"].ticker == "ABC"

    def test_a_failure_is_cached_as_a_failure(self, tmp_path, monkeypatch):
        """Absence and failure are different facts. Recording an unresolvable
        CUSIP as unresolved stops it being re-queried forever and keeps it
        visible in the count -- conflating them is how a coverage figure
        quietly improves."""
        cache = tmp_path / "m.json"
        monkeypatch.setattr(
            "intradyne.research.cusip_map._post",
            lambda batch, key, timeout: [{"data": []} for _ in batch],
        )
        resolve(["ZZZ"], cache_path=cache)
        doc = json.loads(cache.read_text(encoding="utf-8"))
        assert "ZZZ" in doc and doc["ZZZ"]["ticker"] is None

    def test_a_network_failure_keeps_earlier_batches(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def flaky(batch, key, timeout):
            calls["n"] += 1
            if calls["n"] > 1:
                raise TimeoutError("rate limited")
            return [_row(c, "US") and {"data": [_row("T" + c, "US")]} for c in batch]

        monkeypatch.setattr("intradyne.research.cusip_map._post", flaky)
        cusips = [f"C{i:03d}" for i in range(25)]
        resolve(cusips, cache_path=tmp_path / "m.json")
        doc = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
        assert 0 < len(doc) < len(cusips), "partial progress was lost"


class TestSummary:
    def test_it_names_the_precondition(self):
        maps = {
            "A": Mapping_("A", "AAA", "A Corp", "US"),
            "B": Mapping_("B", None, None, None),
        }
        text = summarise(maps)
        assert "50.0%" in text
        assert "80%" in text

    def test_full_resolution_reports_no_failures(self):
        maps = {"A": Mapping_("A", "AAA", "A Corp", "US")}
        assert "unresolved" not in summarise(maps)


class TestRealMapping:
    """Against the committed cache, so a broken commit fails here rather than
    inside a panel build."""

    def test_the_four_that_were_wrong_are_right(self):
        from pathlib import Path

        cache = Path("docs/cusip_ticker_map.json")
        if not cache.exists():
            pytest.skip("no cached mapping committed")
        doc = json.loads(cache.read_text(encoding="utf-8"))
        for cusip, want in (
            ("40434L105", "HPQ"),
            ("22822V101", "CCI"),
            ("03073E105", "COR"),
        ):
            if cusip in doc:
                assert doc[cusip]["ticker"] == want, f"{cusip} resolved wrongly"

    def test_no_eur_denominated_tickers_survived(self):
        from pathlib import Path

        cache = Path("docs/cusip_ticker_map.json")
        if not cache.exists():
            pytest.skip("no cached mapping committed")
        doc = json.loads(cache.read_text(encoding="utf-8"))
        bad = [
            c
            for c, v in doc.items()
            if v.get("ticker") and str(v["ticker"]).endswith("EUR")
        ]
        assert not bad, f"foreign listings leaked through: {bad[:5]}"
