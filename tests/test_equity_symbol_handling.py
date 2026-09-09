"""An equity ticker has to survive the trip from config to the data route.

The project shifted to equities, and the plumbing still assumed crypto in two
places that between them made an equity unusable:

* `allowed_symbols=AAPL` became `AAPL/USDT`, a pair that does not exist. The
  operator was then warned that `AAPL/USDT` was missing from the Shariah
  whitelist and told to add it there -- the wrong remedy, since `risk/shariah`
  permits an equity on a dated screen record and not on the whitelist.
* The data route's symbol pattern *required* a slash, so `AAPL` was refused as
  malformed before any lookup happened.

Neither is a crypto bug. `BTC` -> `BTC/USDT` was deliberate shorthand, and
correct while nothing else traded here; it stopped being unambiguous when
`BTC` and `AAPL` acquired the same shape. So the tests below pin both: the
shorthand still works, and an equity is no longer mangled into a pair.

The route's pattern is a **security control** -- both values are interpolated
into a filename -- so widening it is tested against traversal explicitly rather
than assumed safe because the intent was narrow.
"""

import re

import pytest

from intradyne.api.routes.data import _SYMBOL_RE, _map_symbol
from intradyne.core.config import Settings
from intradyne.risk.shariah import classify_symbol

BACKSLASH = chr(92)


def settings_with(allowed: str) -> Settings:
    s = Settings()
    s.allowed_symbols = allowed
    return s


# ---- the crypto shorthand is preserved -----------------------------------


@pytest.mark.parametrize("raw", ["BTC", "btc", " BTC ", "BTC/USDT"])
def test_a_screened_crypto_base_still_means_its_usdt_pair(raw):
    """Deliberate shorthand, and `test_symbol_universe` pins it end to end.
    Widening for equities must not quietly cost it.

    Case is normalised by `load_symbols` against the whitelist rather than
    here -- this layer settles format, not identity -- so the comparison is
    case-insensitive and the canonical form is asserted below.
    """
    got = settings_with(raw).allowed_instruments()
    assert [s.upper() for s in got] == ["BTC/USDT"]


@pytest.mark.parametrize("raw", ["BTC", "btc", " BTC ", "BTC/USDT"])
def test_the_resolved_universe_is_canonical_whatever_the_operator_typed(raw):
    """Where case actually gets settled. A case mismatch must not read as a
    compliance refusal."""
    assert settings_with(raw).load_symbols() == ["BTC/USDT"]


def test_a_quote_currency_alone_is_still_not_an_instrument():
    assert settings_with("USDT").allowed_instruments() == []


# ---- an equity survives unmangled ----------------------------------------


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "F", "BRK-B"])
def test_an_equity_ticker_is_not_turned_into_a_pair(ticker):
    """The defect: a configured equity became `TICKER/USDT`, an instrument
    nobody named and no venue lists."""
    got = settings_with(ticker).allowed_instruments()
    assert got == [ticker]
    assert not any("/" in s for s in got)


def test_the_two_classes_coexist_in_one_configuration():
    got = settings_with("AAPL,BTC,MSFT").allowed_instruments()
    assert got == ["AAPL", "BTC/USDT", "MSFT"]


def test_the_crypto_allow_list_carries_only_crypto():
    """`ShariahPolicy(allowed_crypto=...)` screens equities by dated record
    instead, so an equity in that list would offer the wrong evidence."""
    s = settings_with("AAPL,BTC")
    assert s.allowed_crypto_list() == ["BTC/USDT"]
    assert all(classify_symbol(x) == "crypto" for x in s.allowed_crypto_list())


# ---- an unscreened equity fails closed, and says the right thing ----------


def test_an_equity_without_a_screen_record_is_not_traded():
    """Fail-closed is the point: no equity has been ruled on here."""
    assert settings_with("AAPL").load_symbols() == []


def test_the_warning_does_not_send_an_equity_to_the_crypto_whitelist():
    """The old message named `AAPL/USDT` and advised editing whitelist.json,
    which is the wrong file for an instrument needing a dated screen.

    loguru does not route through the stdlib, so `caplog` sees nothing here;
    the sink is attached directly.
    """
    from intradyne.core.config import logger as cfg_logger

    records: list[str] = []
    handler_id = cfg_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        settings_with("AAPL").load_symbols()
    finally:
        cfg_logger.remove(handler_id)

    text = "".join(records)
    assert "AAPL" in text, "the operator's own symbol is not named"
    assert "AAPL/USDT" not in text, "the warning invented a pair nobody named"
    assert "screen record" in text, "the operator is not told the real remedy"


# ---- the route pattern is a security control -----------------------------


@pytest.mark.parametrize("sym", ["AAPL", "A", "F", "BRK-B", "BF-B", "BTC/USDT"])
def test_the_route_accepts_real_instruments(sym):
    """`AAPL` was rejected as malformed before any lookup happened."""
    assert _SYMBOL_RE.match(sym), f"{sym} should be a valid symbol"


@pytest.mark.parametrize(
    "sym",
    [
        "../../etc/passwd",
        "..",
        ".",
        "A/../B",
        "A" + BACKSLASH + "B",
        "A.B",
        "A//B",
        "BTC/USDT/X",
        "",
        "A/B/C",
        "%2e%2e",
        "A B",
        "A-",
        "-A",
    ],
)
def test_the_route_still_makes_traversal_unrepresentable(sym):
    """Widening the pattern for equities must not widen it for paths. Both the
    symbol and the timeframe are interpolated into a filename."""
    assert not _SYMBOL_RE.match(sym), f"{sym!r} must not validate"


@pytest.mark.parametrize("sym", ["AAPL", "BRK-B", "BTC/USDT"])
def test_a_mapped_symbol_contributes_no_path_segment(sym):
    mapped = _map_symbol(sym)
    assert "/" not in mapped
    assert BACKSLASH not in mapped
    assert ".." not in mapped


def test_the_pattern_admits_at_most_one_separator():
    """A second separator is the shape traversal needs."""
    assert not _SYMBOL_RE.match("A/B/C")
    assert not _SYMBOL_RE.match("A-B-C")
    assert isinstance(_SYMBOL_RE, re.Pattern)
