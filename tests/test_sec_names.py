"""Name-based ticker recovery, and why it refuses to guess.

Fuzzy name matching is how a resolver puts the wrong company in a panel. The
first version of `cusip_map` took OpenFIGI's first row and mapped Cencora's
CUSIP to `ABG` -- Asbury Automotive, a different company whose prices would
have flowed into the panel unremarked. A scored fuzzy match makes that more
likely, not less, because it always returns something.

So matching here is exact on the normalised key, and a name that does not match
resolves to nothing.
"""

from __future__ import annotations

import json

import pytest

from intradyne.research.sec_names import (  # noqa: F401
    _common_line,
)
from intradyne.research.sec_names import load_registry, normalise, recover


class TestNormalise:
    def test_corporate_forms_are_stripped(self):
        assert normalise("Medtronic PLC") == normalise("Medtronic plc") == "MEDTRONIC"

    def test_punctuation_and_case_do_not_matter(self):
        assert normalise("Super Micro Computer, Inc.") == "SUPER MICRO COMPUTER"

    def test_the_html_ampersand_from_n_port_is_handled(self):
        """N-PORT writes `Tiffany &amp; Co`."""
        assert normalise("Tiffany &amp; Co") == normalise("Tiffany & Co")

    @pytest.mark.parametrize("bad", [None, "", "   ", "!!!", "Inc", "PLC"])
    def test_unusable_names_give_an_empty_key(self, bad):
        """An empty key must never become a bucket that collects unrelated
        issuers."""
        assert normalise(bad) == ""

    def test_distinct_companies_do_not_collapse(self):
        assert normalise("Cooper Cos Inc") != normalise("Cooper-Standard Holdings Inc")


class TestRegistry:
    def _file(self, tmp_path, rows):
        p = tmp_path / "t.json"
        p.write_text(
            json.dumps({str(i): r for i, r in enumerate(rows)}), encoding="utf-8"
        )
        return p

    def test_it_maps_normalised_names_to_tickers(self, tmp_path):
        f = self._file(
            tmp_path, [{"ticker": "HON", "title": "HONEYWELL INTERNATIONAL INC"}]
        )
        assert load_registry(f)["HONEYWELL INTERNATIONAL"] == "HON"

    def test_an_ambiguous_name_is_dropped_not_arbitrated(self, tmp_path):
        """Share classes and preferred lines collapse to one key -- Alphabet
        gives GOOG, GOOGL, GOOGN, GOOGM. Picking one would be a silent coin
        flip over which line's prices enter the panel."""
        f = self._file(
            tmp_path,
            [
                {"ticker": "GOOG", "title": "Alphabet Inc."},
                {"ticker": "GOOGL", "title": "Alphabet Inc."},
            ],
        )
        assert "ALPHABET" not in load_registry(f)

    def test_a_single_ticker_survives_ambiguity_pruning(self, tmp_path):
        f = self._file(
            tmp_path,
            [
                {"ticker": "GOOG", "title": "Alphabet Inc."},
                {"ticker": "GOOGL", "title": "Alphabet Inc."},
                {"ticker": "HON", "title": "Honeywell International Inc"},
            ],
        )
        reg = load_registry(f)
        assert reg["HONEYWELL INTERNATIONAL"] == "HON"

    def test_fetching_without_a_contact_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="contact"):
            load_registry(tmp_path / "absent.json")


class TestRecover:
    def test_an_exact_match_recovers(self):
        reg = {"HONEYWELL INTERNATIONAL": "HON"}
        assert recover({"438516106": "Honeywell International Inc"}, reg) == {
            "438516106": "HON"
        }

    def test_a_near_miss_recovers_nothing(self):
        """`Laboratory Corp of America Hol` versus SEC's `LABCORP HOLDINGS
        INC.` is a rename, not a spelling difference. Leaving it unmatched
        costs one name; guessing risks the wrong issuer."""
        reg = {"LABCORP": "LH"}
        assert recover({"50540R409": "Laboratory Corp of America Hol"}, reg) == {}

    def test_a_delisted_company_recovers_nothing(self):
        """SEC lists current registrants, so an acquired company is absent --
        which is the true answer, not a failure to try hard enough."""
        reg = {"HONEYWELL INTERNATIONAL": "HON"}
        assert recover({"003654100": "ABIOMED Inc"}, reg) == {}

    def test_a_missing_name_is_skipped(self):
        assert recover({"X": None, "Y": ""}, {"": "BAD"}) == {}

    def test_unmatched_cusips_are_absent_not_none(self):
        """Absence is how a caller tells recovered from still-unknown."""
        out = recover({"A": "Known Inc", "B": "Unknown Inc"}, {"KNOWN": "KN"})
        assert out == {"A": "KN"} and "B" not in out


def test_dotted_legal_form_normalises_like_the_undotted_one() -> None:
    """The punctuation asymmetry that lost NXP.

    N-PORT writes "NXP Semiconductors NV", where `NV` is stripped as a
    corporate form. SEC writes "NXP Semiconductors N.V.", which became `N V`
    -- two single letters no form rule matches. Same company, same legal form,
    two different keys.
    """
    assert normalise("NXP Semiconductors N.V.") == normalise("NXP Semiconductors NV")
    assert normalise("Foo S.A.") == normalise("Foo SA")


def test_a_preferred_line_does_not_make_its_common_ambiguous() -> None:
    """SEC files SMCI and SMCIP under one title; dropping both lost SMCI."""
    assert _common_line({"SMCI", "SMCIP"}) == "SMCI"


def test_share_classes_stay_ambiguous_even_when_one_is_a_prefix() -> None:
    """The false positive a bare prefix test would produce.

    SEC lists GOOGL, GOOG, GOOGM and GOOGN all under "Alphabet Inc.". GOOG is
    a prefix of the others and is *not* their common stock -- they are share
    classes. Picking one would bind Class C prices to a Class A holding.
    """
    assert _common_line({"GOOGL", "GOOG", "GOOGM", "GOOGN"}) is None
    assert _common_line({"BRK-A", "BRK-B"}) is None
