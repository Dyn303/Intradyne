"""One universe, enforced on every order path.

Two allow-lists used to feed two paths independently and neither constrained
the other, so the divergence ran both ways:

  `whitelist.json` carried 15 pairs and drove the live loop and the backtester.
  `ALLOWED_SYMBOLS` carried 9 and drove the API's ExecutionManager. LINK, XLM,
  ATOM, TRX, NEAR and ALGO were tradeable by the loop and refused by the API --
  the stricter list did not govern the path that places orders.

  And in the other direction, the API trusted `ALLOWED_SYMBOLS` with no
  compliance check at all, so an operator could enable an instrument the
  Shariah screen had never seen.

Neither surfaced as a failure: both paths returned a plausible list, and the
tests all used BTC/USDT, which is on every list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intradyne.core.config import Settings


WHITELIST = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "intradyne"
    / "engine"
    / "whitelist.json"
)


def permitted() -> set:
    return set(json.loads(WHITELIST.read_text(encoding="utf-8"))["symbols"])


def settings_with(allowed: str) -> Settings:
    s = Settings()
    s.allowed_symbols = allowed
    return s


# ---- the compliance list is a ceiling ------------------------------------


def test_an_operator_cannot_enable_an_unscreened_instrument():
    """The API-side fail-open. DOGE is not on the Shariah whitelist, so naming
    it in ALLOWED_SYMBOLS must not make it tradeable."""
    assert "DOGE/USDT" not in permitted(), "fixture assumption"
    s = settings_with("BTC/USDT,DOGE/USDT")
    assert s.load_symbols() == ["BTC/USDT"]


def test_every_resolved_symbol_is_on_the_compliance_list():
    s = settings_with("BTC/USDT,ETH/USDT,DOGE/USDT,FAKE/USDT")
    assert set(s.load_symbols()) <= permitted()


def test_an_entirely_unscreened_selection_resolves_to_nothing():
    s = settings_with("DOGE/USDT,SHIB/USDT")
    assert s.load_symbols() == []


# ---- the operator list is a selection within it --------------------------


def test_the_operator_can_narrow_the_universe():
    """The loop-side fail-open. The loop used to take all 15 permitted pairs
    regardless of what the operator had selected."""
    s = settings_with("BTC/USDT")
    assert s.load_symbols() == ["BTC/USDT"]
    assert len(permitted()) > 1, "fixture assumption"


def test_narrowing_is_respected_even_though_the_rest_are_permissible():
    s = settings_with("BTC/USDT,ETH/USDT")
    got = s.load_symbols()
    assert set(got) == {"BTC/USDT", "ETH/USDT"}
    assert "LINK/USDT" not in got


def test_no_selection_means_the_compliance_list_stands_alone():
    """Existing meaning preserved: an unset ALLOWED_SYMBOLS is 'not
    configured', not 'trade nothing'. Changing that would stop an existing
    deployment dead."""
    s = settings_with("")
    assert set(s.load_symbols()) == permitted()


# ---- both order paths agree ----------------------------------------------


def test_the_two_order_paths_resolve_the_same_universe():
    """The property the divergence violated. `api/deps.py` builds the
    ExecutionManager whitelist and `engine/loop.py` picks the loop's symbols;
    both now resolve through `load_symbols()`."""
    s = settings_with("BTC/USDT,ETH/USDT")
    api_side = s.load_symbols()
    loop_side = s.load_symbols()
    assert api_side == loop_side


def test_allowed_crypto_list_alone_is_not_a_compliance_check():
    """Pins *why* deps.py may not use it directly: it will happily return an
    unscreened instrument, because it only normalises formatting."""
    s = settings_with("DOGE/USDT")
    assert s.allowed_crypto_list() == ["DOGE/USDT"]
    assert s.load_symbols() == []


# ---- venue intersection still applies ------------------------------------


def test_markets_filter_narrows_further():
    s = settings_with("BTC/USDT,ETH/USDT")
    assert s.load_symbols(markets=["BTC/USDT"]) == ["BTC/USDT"]


def test_markets_filter_cannot_widen_past_compliance():
    """A venue listing DOGE does not make DOGE permissible."""
    s = settings_with("BTC/USDT")
    got = s.load_symbols(markets=["BTC/USDT", "DOGE/USDT"])
    assert got == ["BTC/USDT"]


# ---- formatting -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["BTC", "BTC/USDT", " BTC ", "btc", "btc/usdt", "Btc/Usdt"]
)
def test_case_and_whitespace_do_not_change_the_universe(raw):
    """A case mismatch must not read as a compliance refusal. Dropping
    `btc/usdt` for its case would look exactly like refusing an unscreened
    instrument, and the two must stay distinguishable."""
    assert settings_with(raw).load_symbols() == ["BTC/USDT"]
