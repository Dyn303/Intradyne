from app.portfolio import Portfolio
from app.broker_paper import PaperBroker


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

