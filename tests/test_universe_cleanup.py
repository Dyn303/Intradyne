"""The whitelist, and what it is and is not for.

`config.load_symbols` already states the separation: the whitelist is a
compliance artifact saying what is *permissible*; ALLOWED_SYMBOLS is operator
configuration saying what to trade *today*. So a name that costs too much to
trade is removed from the operator list, never from the whitelist -- deleting
a compliance ruling because a spread widened would mean re-screening the
instrument if it ever tightened again.

A delisted ticker is the other kind of thing. MATIC/USDT is not a live
instrument anywhere: Polygon migrated the token to POL in 2024.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest
from loguru import logger

from intradyne.core.config import Settings


@pytest.fixture
def warnings() -> List[str]:
    """loguru bypasses stdlib logging, so caplog sees nothing."""
    captured: List[str] = []
    sink = logger.add(captured.append, level="WARNING", format="{message}")
    try:
        yield captured
    finally:
        logger.remove(sink)


WHITELIST = json.loads(
    (Path("src/intradyne/engine/whitelist.json")).read_text(encoding="utf-8")
)["symbols"]


def test_the_dead_ticker_is_gone():
    """Not an economic judgement -- MATIC/USDT is not listed on Bitget, and
    the token it named migrated to POL."""
    assert "MATIC/USDT" not in WHITELIST


def test_polygon_was_not_silently_re_added_as_pol():
    """POL/USDT is listed and would trade, but the Shariah ruling was issued
    against MATIC. Re-admitting the asset under its new ticker is a scholarly
    decision, not a rename, so it stays out until someone screens it."""
    assert "POL/USDT" not in WHITELIST


def test_expensive_names_keep_their_compliance_ruling():
    """ADA at 4.51bps and DOT at 11.44bps are refused by the live cost filter.
    That is an economic fact and belongs in ALLOWED_SYMBOLS; the permission to
    trade them, if they ever tighten, must not have to be re-earned."""
    for sym in (
        "ADA/USDT",
        "DOT/USDT",
        "NEAR/USDT",
        "XLM/USDT",
        "ATOM/USDT",
        "ALGO/USDT",
    ):
        assert sym in WHITELIST, f"{sym} lost its compliance ruling to a spread"


def test_the_whitelist_is_still_a_ceiling_the_operator_cannot_raise():
    s = Settings()
    permitted = set(s.compliance_universe())
    assert set(s.load_symbols(list(permitted))) <= permitted


def test_an_unlisted_symbol_is_reported_not_dropped_quietly(warnings):
    """The silence here is how MATIC survived. A delisting is a fact about the
    world that should reach a human."""
    s = Settings()
    permitted = s.compliance_universe()
    assert permitted, "no compliance universe to test against"
    missing = permitted[0]
    venue = permitted[1:]  # the venue lists every name but that one

    kept = s.load_symbols(venue)
    text = chr(10).join(warnings)

    assert missing not in kept
    assert "does not list" in text
    assert missing in text


def test_a_fully_listed_universe_warns_about_nothing(warnings):
    s = Settings()
    s.load_symbols(list(s.compliance_universe()))
    assert "does not list" not in chr(10).join(warnings)
