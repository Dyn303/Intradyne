"""The dust floor.

Sizing is `min(sizer, position_capacity)`, so a position near its cap leaves a
rounding remnant and the next entry is priced in cents. The only guard was
`qty <= 0`, and dust is strictly positive. In a 75-second paper run, 6 of 11
fills were under a dollar -- $0.0049 of BTC, $0.0143 of ETH -- against a venue
minimum of $1.00. Paper filled them and charged taker fees; the live exchange
would have rejected them. That is a paper/live divergence, not a rounding
detail: the equity curve was shaped by orders that could not exist.
"""

from __future__ import annotations

import asyncio

import pytest

from intradyne.core.config import Settings
from intradyne.engine.broker_paper import PaperBroker
from intradyne.engine.execution import ExecContext, ExecutionManager
from intradyne.engine.loop import venue_min_notionals
from intradyne.engine.portfolio import Portfolio


SYM = "BTC/USDT"
PX = 80_771.161002


class FakeLedger(list):
    """The real ledger takes `append(event, payload)`, not one argument."""

    def append(self, event, payload=None):  # type: ignore[override]
        list.append(self, (event, payload))


def _exec(**kw):
    portfolio = Portfolio()
    portfolio.balances["USDT"] = 10_000.0
    ctx = ExecContext(
        portfolio=portfolio,
        paper=PaperBroker(portfolio),
        ledger=FakeLedger(),
        whitelist=[SYM],
        fast_mode=True,
        **kw,
    )
    return ExecutionManager(ctx), ctx


def _submit(mgr, qty):
    return asyncio.run(
        mgr.submit(
            SYM,
            "buy",
            "market",
            qty,
            None,
            {"symbol": SYM, "last": PX, "bid": PX, "ask": PX},
            "test",
            {},
            {},
        )
    )


def test_dust_is_refused():
    """$0.0049 of BTC -- an actual fill from the paper run."""
    mgr, _ = _exec()
    qty = 0.0049 / PX
    r = _submit(mgr, qty)
    assert r["status"] == "blocked"
    assert r["action"] == "below_min_notional"


def test_a_real_order_still_goes_through():
    mgr, _ = _exec()
    r = _submit(mgr, 169.56 / PX)
    assert r["status"] != "blocked", r


def test_the_refusal_reaches_the_ledger():
    """A blocked order that leaves no trace defeats an audit ledger, which is
    the reason the gate's own refusals are recorded."""
    mgr, ctx = _exec()
    _submit(mgr, 0.0049 / PX)
    blocked = [e for e in ctx.ledger if "below_min_notional" in str(e)]
    assert blocked, f"nothing recorded: {ctx.ledger}"


def test_the_floor_is_per_symbol_and_beats_the_default():
    mgr, ctx = _exec(default_min_notional=1.0)
    ctx.min_notional[SYM] = 500.0
    assert _submit(mgr, 169.56 / PX)["action"] == "below_min_notional"


def test_zero_floor_disables_it():
    mgr, ctx = _exec(default_min_notional=0.0)
    assert _submit(mgr, 0.0049 / PX)["status"] != "blocked"


def test_default_matches_the_configured_fallback():
    assert (
        ExecContext(
            portfolio=Portfolio(),
            paper=PaperBroker(Portfolio()),
            ledger=FakeLedger(),
            whitelist=[],
        ).default_min_notional
        == Settings().min_order_notional
    )


class TestVenueFloors:
    """`limits.cost.min` is the venue's own number. Reading it beats
    configuring one, because the floor is a property of the exchange."""

    def test_reads_cost_min(self):
        markets = {SYM: {"limits": {"cost": {"min": 1.0}}}}
        assert venue_min_notionals(markets, [SYM]) == {SYM: 1.0}

    def test_skips_a_market_declaring_only_an_amount(self):
        """An amount minimum cannot be converted without a price, so the
        symbol takes the configured fallback rather than a guess."""
        markets = {SYM: {"limits": {"amount": {"min": 0.0001}}}}
        assert venue_min_notionals(markets, [SYM]) == {}

    @pytest.mark.parametrize(
        "markets",
        [
            None,
            {},
            {SYM: None},
            {SYM: {}},
            {SYM: {"limits": None}},
            {SYM: {"limits": {"cost": None}}},
            {SYM: {"limits": {"cost": {}}}},
            {SYM: {"limits": {"cost": {"min": None}}}},
            {SYM: {"limits": {"cost": {"min": 0}}}},
        ],
    )
    def test_missing_metadata_yields_no_floor_rather_than_a_crash(self, markets):
        assert venue_min_notionals(markets, [SYM]) == {}
