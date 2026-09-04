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


def _submit_side(mgr, qty, side):
    return asyncio.run(
        mgr.submit(
            SYM,
            side,
            "market",
            qty,
            None,
            {"symbol": SYM, "last": PX, "bid": PX, "ask": PX},
            "test",
            {},
            {},
        )
    )


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


class TestStrandedPositions:
    """A refused *exit* is a different event from a refused entry.

    The router resubmits the exit on every tick for as long as the stop stays
    breached. Ledger-appending each refusal grew the hash chain without bound
    -- once per tick, forever, for a position worth cents.

    Refusing is still correct: the venue will not accept a sub-minimum sell
    either, so the holding genuinely is stranded, and filling it in paper
    would put a trade in the equity curve that live could never produce. The
    entry floor stops these being opened; this reports the ones that exist.
    """

    def _with_dust(self):
        mgr, ctx = _exec()
        ctx.min_notional[SYM] = 1.0
        ctx.portfolio.buy(SYM, 0.40 / PX, PX)
        return mgr, ctx

    def _exit(self, mgr, ctx):
        return _submit_side(mgr, ctx.portfolio.get_position(SYM).base, "sell")

    def test_a_dust_position_cannot_be_closed(self):
        """Stated rather than hidden: the stop-loss on such a position will
        not execute, because the exchange would not accept the order."""
        mgr, ctx = self._with_dust()
        r = self._exit(mgr, ctx)
        assert r["status"] == "blocked"
        assert r["stranded"] is True

    def test_the_refusal_is_recorded_once_not_every_tick(self):
        mgr, ctx = self._with_dust()
        for _ in range(50):
            self._exit(mgr, ctx)
        entries = [e for e in ctx.ledger if "below_min_notional" in str(e)]
        assert len(entries) == 1, f"ledger grew to {len(entries)} entries"

    def test_a_refused_entry_is_still_recorded_every_time(self):
        """Entries are not retried tick-on-tick the way a breached stop is,
        and each one is a distinct decision worth auditing."""
        mgr, ctx = _exec()
        for _ in range(5):
            _submit(mgr, 0.0049 / PX)
        entries = [e for e in ctx.ledger if "below_min_notional" in str(e)]
        assert len(entries) == 5

    def test_a_position_that_grows_back_is_closable_again(self):
        mgr, ctx = self._with_dust()
        self._exit(mgr, ctx)
        ctx.portfolio.buy(SYM, 200.0 / PX, PX)
        assert self._exit(mgr, ctx)["status"] != "blocked"
        # ...and having recovered, it can strand again and report again.
        ctx.portfolio.positions[SYM].base = 0.40 / PX
        assert self._exit(mgr, ctx)["stranded"] is True
        entries = [e for e in ctx.ledger if "below_min_notional" in str(e)]
        assert len(entries) == 2


class TestEntryFloor:
    """The venue floor stops orders the exchange would reject. It does not
    stop orders that are merely pointless.

    Observed in an hour of paper trading after the venue floor landed: 3 of
    288 fills were $1.01, $1.13 and $1.22 -- valid orders, accepted by the
    exchange, spending an order slot and rate-limit budget to move a dollar.

    Buys only. An exit is worth making at any size, because the alternative is
    holding the position, and a floor that blocks exits is how #48 stranded
    positions whose stops then could not fire.
    """

    def _mgr(self, floor=15.0):
        mgr, ctx = _exec(min_entry_notional=floor)
        ctx.min_notional[SYM] = 1.0
        return mgr, ctx

    def test_a_dollar_entry_is_refused(self):
        mgr, _ = self._mgr()
        r = _submit(mgr, 1.0146 / PX)  # an actual observed fill
        assert r["status"] == "blocked"
        assert r["action"] == "below_min_entry"

    def test_a_full_size_entry_passes(self):
        mgr, _ = self._mgr()
        assert _submit(mgr, 169.0 / PX)["status"] != "blocked"

    def test_an_exit_of_the_same_size_is_untouched(self):
        """The asymmetry that makes this a separate setting."""
        mgr, ctx = self._mgr()
        ctx.portfolio.buy(SYM, 5.0 / PX, PX)
        r = _submit_side(mgr, ctx.portfolio.get_position(SYM).base, "sell")
        assert r["status"] != "blocked", r

    def test_zero_disables_it(self):
        mgr, _ = self._mgr(floor=0.0)
        assert _submit(mgr, 1.0146 / PX)["status"] != "blocked"

    def test_the_venue_floor_still_bites_underneath(self):
        """A sub-venue-minimum order is refused for the venue's reason, not
        this one -- the two must stay distinguishable in the ledger."""
        mgr, _ = self._mgr()
        assert _submit(mgr, 0.0049 / PX)["action"] == "below_min_notional"

    def test_the_refusal_is_recorded(self):
        mgr, ctx = self._mgr()
        _submit(mgr, 1.0146 / PX)
        assert [e for e in ctx.ledger if "below_min_entry" in str(e)]

    def test_the_default_is_a_twentieth_of_a_full_order(self):
        from intradyne.core.config import Settings

        s = Settings()
        assert s.min_entry_notional == 15.0
        assert s.min_entry_notional > s.min_order_notional, (
            "the policy floor must sit above the venue floor to do anything"
        )
