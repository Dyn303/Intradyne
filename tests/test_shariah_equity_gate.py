"""Equity tickers must be screened, and refused when they have not been.

The defect these pin: business screening was guarded by
``if is_crypto_symbol(symbol)``, and that function is ``"/" in symbol``. An
order for ``AAPL`` skipped the allow-list and the tag screen and was
**permitted** -- it failed open. The whole suite stayed green because every
other test uses ``BTC/USDT``-shaped symbols, which is exactly why these are
written against bare tickers.

`test_equity_without_a_screen_is_refused` is the one that fails on the old
code. The rest guard the parts that had to keep working.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from intradyne.risk.shariah import (
    DEFAULT_MAX_SCREEN_AGE_DAYS,
    ScreenResult,
    ShariahPolicy,
)


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).strftime("%Y-%m-%d")


def _passing(sym_age: int = 1) -> ScreenResult:
    return ScreenResult(
        passed=True, as_of=_days_ago(sym_age), standard="AAOIFI-style (test)"
    )


# ---- the fix -------------------------------------------------------------


def test_equity_without_a_screen_is_refused():
    """The regression. On the old code this returned (True, "ok")."""
    ok, reason = ShariahPolicy().check("AAPL")
    assert ok is False
    assert "AAPL" in reason and "screen" in reason.lower()


@pytest.mark.parametrize("sym", ["AAPL", "MSFT", "PORNCO", "BUD", "MO"])
def test_no_bare_ticker_is_admitted_by_default(sym):
    """An empty policy permits no equity at all. Prohibited-activity names are
    in here deliberately: under the old code every one of them passed."""
    ok, _ = ShariahPolicy().check(sym)
    assert ok is False


def test_equity_with_a_current_passing_screen_is_permitted():
    pol = ShariahPolicy(equity_screen={"AAPL": _passing()})
    ok, reason = pol.check("AAPL")
    assert ok is True and reason == "ok"


def test_screen_lookup_is_case_insensitive():
    pol = ShariahPolicy(equity_screen={"aapl": _passing()})
    assert pol.check("AAPL")[0] is True
    assert pol.check("aapl")[0] is True


# ---- refusals name what failed ------------------------------------------


def test_failed_screen_names_the_standard_and_the_reason():
    pol = ShariahPolicy(
        equity_screen={
            "XYZ": ScreenResult(
                passed=False,
                as_of=_days_ago(1),
                standard="AAOIFI-style",
                reason="debt/mcap 0.61 over 0.30",
            )
        }
    )
    ok, reason = pol.check("XYZ")
    assert ok is False
    assert "AAOIFI-style" in reason
    assert "0.61" in reason


def test_stale_screen_is_refused_and_says_how_stale():
    age = DEFAULT_MAX_SCREEN_AGE_DAYS + 30
    pol = ShariahPolicy(equity_screen={"AAPL": _passing(sym_age=age)})
    ok, reason = pol.check("AAPL")
    assert ok is False
    assert "stale" in reason and str(age) in reason


def test_screen_just_inside_the_age_limit_still_passes():
    pol = ShariahPolicy(
        equity_screen={"AAPL": _passing(sym_age=DEFAULT_MAX_SCREEN_AGE_DAYS - 1)}
    )
    assert pol.check("AAPL")[0] is True


def test_unreadable_screen_date_is_refused_rather_than_ignored():
    pol = ShariahPolicy(
        equity_screen={
            "AAPL": ScreenResult(passed=True, as_of="whenever", standard="x")
        }
    )
    ok, reason = pol.check("AAPL")
    assert ok is False
    assert "unreadable" in reason


def test_max_age_is_configurable():
    pol = ShariahPolicy(
        equity_screen={"AAPL": _passing(sym_age=10)}, max_screen_age_days=5
    )
    assert pol.check("AAPL")[0] is False


# ---- blocked tags now reach equities too --------------------------------


def test_blocked_tags_apply_to_equities():
    """Tag exclusion used to sit inside the crypto-only branch, so an equity
    tagged 'gambling' was permitted."""
    pol = ShariahPolicy(equity_screen={"XYZ": _passing()})
    ok, reason = pol.check("XYZ", meta={"tags": ["gambling"]})
    assert ok is False
    assert "gambling" in reason


# ---- crypto behaviour is unchanged --------------------------------------


def test_crypto_allow_list_still_enforced():
    pol = ShariahPolicy(allowed_crypto=["BTC/USDT"])
    assert pol.check("BTC/USDT")[0] is True
    ok, reason = pol.check("XRP/USDT")
    assert ok is False and "not in allowed list" in reason


def test_empty_crypto_allow_list_is_still_permissive():
    """Deliberately preserved. An empty list means none was configured, and
    changing that would refuse every order in an existing deployment. Equities
    have no such history, which is why they fail closed instead."""
    assert ShariahPolicy().check("BTC/USDT")[0] is True


def test_crypto_blocked_tags_still_enforced():
    pol = ShariahPolicy(allowed_crypto=["BTC/USDT"])
    ok, reason = pol.check("BTC/USDT", meta={"tags": ["gambling"]})
    assert ok is False and "gambling" in reason


# ---- the other rules still apply to equities ----------------------------


def test_spot_only_applies_before_screening():
    ok, reason = ShariahPolicy(equity_screen={"AAPL": _passing()}).check(
        "AAPL", params={"leverage": 3}
    )
    assert ok is False
    assert "leverage" in reason


def test_long_only_applies_to_a_screened_equity():
    pol = ShariahPolicy(equity_screen={"AAPL": _passing()})
    ok, reason = pol.check("AAPL", side="sell", base_inventory=0.0)
    assert ok is False and "short selling" in reason


def test_sell_with_unknown_inventory_still_fails_closed():
    pol = ShariahPolicy(equity_screen={"AAPL": _passing()})
    ok, reason = pol.check("AAPL", side="sell", base_inventory=None)
    assert ok is False and "inventory unknown" in reason


# ---- through the real gate, into the ledger ------------------------------


def test_unscreened_equity_is_blocked_by_the_guardrail_and_ledgered(tmp_path):
    """A refusal is only worth anything if it reaches the order path and is
    recorded. This goes through `Guardrails.gate_trade`, not the policy
    object, and checks the hash chain still verifies afterwards."""
    from intradyne.core.ledger import Ledger
    from intradyne.risk.guardrails import Guardrails, OrderReq

    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    gr = Guardrails(
        price_feed=None,
        risk_data=None,
        ledger=ledger,
        shariah=ShariahPolicy(allowed_crypto=["BTC/USDT"]),
    )

    action, reasons, _ = gr.gate_trade(OrderReq(symbol="AAPL", side="buy", qty=1.0))
    assert action == "block", f"unscreened equity was not blocked: {reasons}"
    assert any("screen" in r.lower() for r in reasons), reasons

    raw = Path(ledger.path).read_text(encoding="utf-8").splitlines()
    entries = [json.loads(x) for x in raw if x.strip()]
    breaches = [e for e in entries if e.get("type") == "compliance"]
    assert breaches, "the refusal was not written to the ledger"
    assert ledger.verify_chain()[0] is True


class _FlatRisk:
    """A calm book: no drawdown, no volatility, so nothing downstream of the
    compliance step has an opinion."""

    def equity_series_30d(self):
        base = datetime.now(timezone.utc)
        return [(base - timedelta(days=i), 10_000.0) for i in range(30, 0, -1)]

    def equity_daily_returns_30d(self):
        return [0.0] * 30


class _FlatPrice:
    def get_price(self, symbol, at=None):
        return 100.0


def test_screened_equity_passes_the_guardrail(tmp_path):
    from intradyne.core.ledger import Ledger
    from intradyne.risk.guardrails import Guardrails, OrderReq

    gr = Guardrails(
        price_feed=_FlatPrice(),
        risk_data=_FlatRisk(),
        ledger=Ledger(path=str(tmp_path / "ledger.jsonl")),
        shariah=ShariahPolicy(
            allowed_crypto=["BTC/USDT"], equity_screen={"AAPL": _passing()}
        ),
    )
    action, reasons, _ = gr.gate_trade(OrderReq(symbol="AAPL", side="buy", qty=1.0))
    assert action != "block", reasons


# ---- ScreenResult ---------------------------------------------------------


def test_age_days_reports_none_for_an_unparseable_date():
    assert ScreenResult(True, "not-a-date", "x").age_days() is None


def test_age_days_counts_from_the_screen_date():
    assert ScreenResult(True, _days_ago(7), "x").age_days() == 7
