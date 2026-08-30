import pytest

from intradyne.engine.portfolio import Portfolio
from intradyne.engine.broker_paper import PaperBroker


def test_market_buy_and_sell_with_fees_slippage():
    pf = Portfolio()
    pb = PaperBroker(pf, slippage_bps=2)
    l1 = {"bid": 100.0, "ask": 101.0, "last": 100.5, "ts": 0}
    # Buy 1 unit market -> expect pay ask + slippage
    o1 = pb.place_order("BTC/USDT", "buy", "market", 1.0, None, l1)
    assert o1.status == "filled"
    assert abs(pf.positions["BTC/USDT"].base - 1.0) < 1e-9
    # Sell 1 unit market -> receive bid - slippage
    o2 = pb.place_order("BTC/USDT", "sell", "market", 1.0, None, l1)
    assert o2.status == "filled"
    # Check quote balance reduced by fees on both sides; realized PnL close to negative due to spread + slippage + fees
    eq = pf.balances["USDT"]
    # Started 10_000, should be slightly below due to costs
    assert eq < 10_000.0


def test_limit_buy_fill_when_touched():
    pf = Portfolio()
    pb = PaperBroker(pf, slippage_bps=0)
    l1 = {"bid": 100.0, "ask": 100.5, "last": 100.5, "ts": 0}
    # Limit buy at or above ask should fill as maker
    o1 = pb.place_order("ETH/USDT", "buy", "limit", 2.0, 100.5, l1)
    assert o1.status == "filled"
    assert abs(pf.positions["ETH/USDT"].base - 2.0) < 1e-9


def _l1(px, ts=0.0):
    return {"symbol": "BTC/USDT", "bid": px, "ask": px, "last": px, "ts": ts}


def test_marketable_limit_is_a_taker_fill():
    """A limit that already crosses the spread takes liquidity.

    Booking it as maker -- which is what happened -- credits a rebate that was
    never earned and makes maker execution look free.
    """
    pf = Portfolio()
    b = PaperBroker(pf, slippage_bps=0)
    # Buy limit above the ask: marketable, so it lifts the offer.
    order = b.place_order("BTC/USDT", "buy", "limit", 1.0, 110.0, _l1(100.0))
    assert order.status == "filled"
    assert order.filled_as_maker is False


def test_resting_limit_fills_as_maker_when_the_market_comes_to_it():
    pf = Portfolio()
    b = PaperBroker(pf, slippage_bps=0)
    # Posted below the market: rests rather than filling.
    order = b.place_order("BTC/USDT", "buy", "limit", 1.0, 99.0, _l1(100.0))
    assert order.status == "open"
    assert pf.get_position("BTC/USDT").base == 0.0

    # Price falls to the posted level: now it fills, and as maker.
    b.on_tick(_l1(98.5, ts=1.0))
    assert order.status == "filled"
    assert order.filled_as_maker is True
    assert pf.get_position("BTC/USDT").base == pytest.approx(1.0)


def test_a_resting_order_that_is_never_reached_expires():
    """A passive order that never fills must go away, or the book fills with
    stale intentions."""
    pf = Portfolio()
    b = PaperBroker(pf, slippage_bps=0, limit_ttl_s=30.0)
    order = b.place_order("BTC/USDT", "buy", "limit", 1.0, 99.0, _l1(100.0, ts=0.0))
    assert order.status == "open"

    b.on_tick(_l1(100.5, ts=10.0))  # still resting, within ttl
    assert order.status == "open"
    b.on_tick(_l1(100.5, ts=40.0))  # past ttl
    assert order.status == "expired"
    assert pf.get_position("BTC/USDT").base == 0.0


def test_market_orders_remain_taker():
    pf = Portfolio()
    b = PaperBroker(pf, slippage_bps=2)
    order = b.place_order("BTC/USDT", "buy", "market", 1.0, None, _l1(100.0))
    assert order.status == "filled"
    assert order.filled_as_maker is False
    # Slippage applied against the taker.
    assert pf.get_position("BTC/USDT").avg_price > 100.0
