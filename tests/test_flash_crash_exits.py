"""The flash-crash shield, and what it was disarming.

`flash_crash_check` fires at a 30% drop from the 1h high (`config.py:61`).
The handler returned on that check, and the return sat *above* the exit
handling -- so a symbol in free-fall stopped evaluating its own stops. The
shield did not protect the book; it disarmed the thing protecting the book,
at exactly the moment that thing mattered.
"""

from __future__ import annotations

import asyncio

from intradyne.engine.broker_paper import PaperBroker
from intradyne.engine.execution import ExecContext, ExecutionManager
from intradyne.engine.portfolio import Portfolio
from intradyne.engine.risk import RiskManager
from intradyne.engine.router import StrategyRouter


SYM = "BTC/USDT"


class FakeLedger(list):
    def append(self, event, payload=None):  # type: ignore[override]
        list.append(self, (event, payload))


def _router(drop: float = 0.30):
    portfolio = Portfolio()
    portfolio.balances["USDT"] = 100_000.0
    risk = RiskManager(
        max_pos_pct=0.5,
        per_trade_sl_pct=0.02,
        tp_pct=0.04,
        dd_soft=0.9,
        dd_hard=0.95,
        flash_crash_drop_1h=drop,
        max_concurrent_pos=5,
        kill_switch_breaches=99,
    )
    ctx = ExecContext(
        portfolio=portfolio,
        paper=PaperBroker(portfolio),
        ledger=FakeLedger(),
        whitelist=[SYM],
        fast_mode=True,
    )
    return StrategyRouter([SYM], risk, ExecutionManager(ctx), portfolio), portfolio


def _tick(router, px, ts):
    asyncio.run(
        router.on_tick({"symbol": SYM, "last": px, "bid": px, "ask": px, "ts": ts})
    )


def _open_position(router, portfolio, entry=100.0):
    """Put a real position on the book with a stop 2% below entry."""
    portfolio.buy(SYM, 10.0, entry)
    sl, tp = router.risk.sl_tp_levels(entry)
    router.stops[SYM] = (sl, tp)
    router.entry_ts[SYM] = 0.0
    router._entry_price[SYM] = entry
    router._entry_high[SYM] = entry
    return sl


def test_the_stop_fires_during_a_crash():
    """The regression. A 30% drop trips the shield; the stop sits 2% below
    entry and is breached many times over, so it must execute."""
    router, portfolio = _router()
    _open_position(router, portfolio)

    _tick(router, 100.0, 1.0)  # establishes the 1h high
    _tick(router, 65.0, 2.0)  # -35%: shield fires, stop deeply breached

    assert router.risk.flash_crash_check(SYM, 3.0, 65.0), "shield should be up"
    assert portfolio.get_position(SYM).base == 0, (
        "position survived a 35% drop with its stop breached -- the shield "
        "was blocking the exit"
    )


def test_no_new_entry_is_taken_during_a_crash():
    """The shield's actual job, which must survive the fix."""
    router, portfolio = _router()
    _tick(router, 100.0, 1.0)
    before = portfolio.balances["USDT"]
    for i in range(200):
        _tick(router, 65.0, 2.0 + i)
    assert portfolio.get_position(SYM).base == 0
    assert portfolio.balances["USDT"] == before, "cash moved: an entry was taken"


def test_an_ordinary_dip_still_stops_out_normally():
    """A 3% move does not trip the shield, and the stop still works -- so the
    test above is not passing merely because stops always fire."""
    router, portfolio = _router()
    _open_position(router, portfolio)
    _tick(router, 100.0, 1.0)
    _tick(router, 97.0, 2.0)
    assert not router.risk.flash_crash_check(SYM, 3.0, 97.0)
    assert portfolio.get_position(SYM).base == 0


def test_a_quiet_market_is_untouched():
    router, portfolio = _router()
    _open_position(router, portfolio)
    _tick(router, 100.0, 1.0)
    _tick(router, 100.5, 2.0)
    assert portfolio.get_position(SYM).base > 0, "stop fired with no reason to"
