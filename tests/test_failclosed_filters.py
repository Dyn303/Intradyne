"""Controls that malfunction must refuse, not allow.

The spread filter and the entry cooldown were each wrapped in
`except Exception: pass`, so a throw skipped the `continue` and the entry
went through anyway -- an order at any spread, or one inside a cooldown. A
control that permits the thing it exists to refuse whenever it malfunctions
is not a control.

The spread filter carried a second hole of the same kind: it required
`bid > 0 and ask > 0` and fell through otherwise, so a quote supplying only
`last` disabled it silently.
"""

from __future__ import annotations

from intradyne.engine.broker_paper import PaperBroker
from intradyne.engine.execution import ExecContext, ExecutionManager
from intradyne.engine.portfolio import Portfolio
from intradyne.engine.risk import RiskManager
from intradyne.engine.router import StrategyRouter


SYM = "BTC/USDT"


class FakeLedger(list):
    def append(self, event, payload=None):  # type: ignore[override]
        list.append(self, (event, payload))


def _router():
    pf = Portfolio()
    pf.balances["USDT"] = 100_000.0
    risk = RiskManager(
        max_pos_pct=0.5,
        per_trade_sl_pct=0.02,
        tp_pct=0.04,
        dd_soft=0.9,
        dd_hard=0.95,
        flash_crash_drop_1h=0.9,
        max_concurrent_pos=5,
        kill_switch_breaches=99,
    )
    ctx = ExecContext(
        portfolio=pf,
        paper=PaperBroker(pf),
        ledger=FakeLedger(),
        whitelist=[SYM],
        fast_mode=True,
    )
    r = StrategyRouter([SYM], risk, ExecutionManager(ctx), pf)
    r._max_spread_bps = 4
    return r


def _q(bid, ask, last=100.0):
    return {"symbol": SYM, "bid": bid, "ask": ask, "last": last, "ts": 1.0}


class TestSpreadFilter:
    def test_a_tight_spread_passes(self):
        assert _router()._spread_too_wide(SYM, _q(99.99, 100.01)) is False

    def test_a_wide_spread_is_refused(self):
        """DOT on Bitget: 11.38bps against a bound of 4."""
        assert _router()._spread_too_wide(SYM, _q(99.94, 100.06)) is True

    def test_a_quote_with_no_bid_or_ask_is_refused(self):
        """It used to fall through, so a feed supplying only `last` disabled
        the filter without saying so."""
        assert _router()._spread_too_wide(SYM, {"symbol": SYM, "last": 100.0}) is True

    def test_an_unreadable_quote_is_refused(self):
        assert _router()._spread_too_wide(SYM, _q("nonsense", 100.0)) is True

    def test_zero_bound_still_disables_it(self):
        r = _router()
        r._max_spread_bps = 0
        assert r._spread_too_wide(SYM, _q(90.0, 110.0)) is False


class TestCooldown:
    def test_inside_the_cooldown_is_refused(self):
        r = _router()
        r._entry_cooldown_s = 60
        r._cooldown_until[SYM] = 500.0
        assert r._in_cooldown(SYM, 400.0) is True

    def test_after_the_cooldown_passes(self):
        r = _router()
        r._entry_cooldown_s = 60
        r._cooldown_until[SYM] = 500.0
        assert r._in_cooldown(SYM, 600.0) is False

    def test_unreadable_cooldown_state_is_refused(self):
        """A cooldown that cannot be read has not been shown to have expired."""
        r = _router()
        r._entry_cooldown_s = 60
        r._cooldown_until[SYM] = "not a number"  # type: ignore[assignment]
        assert r._in_cooldown(SYM, 600.0) is True

    def test_disabled_cooldown_passes(self):
        r = _router()
        r._entry_cooldown_s = 0
        r._cooldown_until[SYM] = 1e18
        assert r._in_cooldown(SYM, 0.0) is False
