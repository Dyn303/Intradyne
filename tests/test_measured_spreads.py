"""Loading the committed spread measurements.

The central rule: a symbol with no reading takes the configured fallback.
Absence of a measurement is not evidence of a tight book, and treating it as
one is how a backtest ends up pricing DOT like BTC.
"""

from __future__ import annotations

import json

import pytest

from intradyne.core.spreads import load_measured_spreads


def _write(tmp_path, doc):
    p = tmp_path / "spreads.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _doc(date="2026-09-04", exchange="bitget", **symbols):
    return {
        date: {
            "exchange": exchange,
            "symbols": {s: {"spread_bps": v} for s, v in symbols.items()},
        }
    }


class TestLoading:
    def test_reads_the_measured_medians(self, tmp_path):
        p = _write(tmp_path, _doc(**{"BTC/USDT": 0.0012, "DOT/USDT": 11.44}))
        assert load_measured_spreads(p) == {"BTC/USDT": 0.0012, "DOT/USDT": 11.44}

    def test_the_most_recent_date_wins(self, tmp_path):
        doc = _doc("2026-01-01", **{"BTC/USDT": 9.0})
        doc.update(_doc("2026-09-04", **{"BTC/USDT": 0.0012}))
        assert load_measured_spreads(_write(tmp_path, doc))["BTC/USDT"] == 0.0012

    def test_a_different_venue_is_not_borrowed(self, tmp_path):
        """Spreads are a property of the book, so a Kraken reading says
        nothing about Bitget."""
        p = _write(tmp_path, _doc(exchange="kraken", **{"BTC/USDT": 0.5}))
        assert load_measured_spreads(p, exchange="bitget") == {}

    def test_a_venue_match_is_used(self, tmp_path):
        p = _write(tmp_path, _doc(exchange="bitget", **{"BTC/USDT": 0.5}))
        assert load_measured_spreads(p, exchange="bitget") == {"BTC/USDT": 0.5}


class TestAbsence:
    """Every one of these must yield an empty map, so the caller falls back
    rather than pricing an unmeasured book at zero."""

    def test_a_missing_file(self, tmp_path):
        assert load_measured_spreads(tmp_path / "nope.json") == {}

    def test_malformed_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_measured_spreads(p) == {}

    @pytest.mark.parametrize(
        "doc",
        [
            {},
            {"2026-09-04": None},
            {"2026-09-04": {"exchange": "bitget"}},
            {"2026-09-04": {"exchange": "bitget", "symbols": None}},
            {"2026-09-04": {"exchange": "bitget", "symbols": {}}},
            {"2026-09-04": {"exchange": "bitget", "symbols": {"BTC/USDT": None}}},
            {"2026-09-04": {"exchange": "bitget", "symbols": {"BTC/USDT": {}}}},
            {
                "2026-09-04": {
                    "exchange": "bitget",
                    "symbols": {"BTC/USDT": {"spread_bps": None}},
                }
            },
            {
                "2026-09-04": {
                    "exchange": "bitget",
                    "symbols": {"BTC/USDT": {"spread_bps": -1.0}},
                }
            },
        ],
    )
    def test_malformed_shapes(self, tmp_path, doc):
        assert load_measured_spreads(_write(tmp_path, doc)) == {}

    def test_an_omitted_symbol_is_simply_absent(self, tmp_path):
        """`measure_spreads.py` omits a symbol it could not read -- MATIC is
        not listed on Bitget at all -- rather than recording a zero."""
        p = _write(tmp_path, _doc(**{"BTC/USDT": 0.0012}))
        loaded = load_measured_spreads(p)
        assert "MATIC/USDT" not in loaded


class TestCommittedMeasurements:
    """The real file, so a broken commit fails here rather than in a backtest."""

    def test_it_loads_and_covers_the_traded_names(self):
        m = load_measured_spreads(exchange="bitget")
        assert m, "docs/spread_measurements.json did not load"
        for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "DOT/USDT"):
            assert sym in m, f"{sym} unmeasured"

    def test_the_ordering_that_motivates_per_symbol_pricing(self):
        """If these collapsed to one number the map would be pointless."""
        m = load_measured_spreads(exchange="bitget")
        assert m["BTC/USDT"] < m["SOL/USDT"] < m["ADA/USDT"] < m["DOT/USDT"]
        assert m["DOT/USDT"] > 10.0, "DOT should be the expensive one"

    def test_every_value_is_a_plausible_spread(self):
        for sym, v in load_measured_spreads(exchange="bitget").items():
            assert 0.0 <= v < 1000.0, f"{sym} = {v}"
