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


def _maker_ctx(portfolio, broker):
    import os
    import tempfile

    from intradyne.core.ledger import Ledger
    from intradyne.engine.execution import ExecContext

    return ExecContext(
        portfolio=portfolio,
        paper=broker,
        ledger=Ledger(path=os.path.join(tempfile.mkdtemp(), "l.jsonl")),
        whitelist=["BTC/USDT"],
        fast_mode=True,
        execution_mode="maker",
    )


def test_only_one_resting_entry_per_symbol():
    """The strategy decides to enter from position size, which stays zero
    while an order rests unfilled, so it re-submits every tick. Those queue
    and all fill together on the first dip, producing a position many times
    the intended size -- measured at ~12x the taker run's notional.
    """
    import asyncio

    from intradyne.engine.execution import ExecutionManager

    pf = Portfolio()
    broker = PaperBroker(pf, slippage_bps=0, limit_ttl_s=600)
    em = ExecutionManager(_maker_ctx(pf, broker))
    quote = {"symbol": "BTC/USDT", "bid": 99.0, "ask": 100.0, "last": 100.0, "ts": 0.0}

    async def go():
        a = await em.submit("BTC/USDT", "buy", "market", 1.0, None, quote, "s", {}, {})
        b = await em.submit("BTC/USDT", "buy", "market", 1.0, None, quote, "s", {}, {})
        return a, b

    first, second = asyncio.run(go())
    assert first.get("resting") is True
    assert second.get("status") == "pending"
    assert second.get("action") == "resting_order_exists"
    assert len(broker.open_orders("BTC/USDT")) == 1


def test_exits_always_cross_even_in_maker_mode():
    """A passive stop is not a stop: it rests unfilled exactly when the
    market is running away, leaving the position open."""
    import asyncio

    from intradyne.engine.execution import ExecutionManager

    pf = Portfolio()
    pf.buy("BTC/USDT", qty=1.0, price=100.0)
    broker = PaperBroker(pf, slippage_bps=0)
    em = ExecutionManager(_maker_ctx(pf, broker))
    quote = {"symbol": "BTC/USDT", "bid": 95.0, "ask": 96.0, "last": 95.0, "ts": 0.0}

    async def go():
        return await em.submit(
            "BTC/USDT", "sell", "market", 1.0, None, quote, "stop_exit", {}, {}
        )

    result = asyncio.run(go())
    assert result.get("status") == "filled", "an exit must not rest"
    assert pf.get_position("BTC/USDT").base == pytest.approx(0.0)
