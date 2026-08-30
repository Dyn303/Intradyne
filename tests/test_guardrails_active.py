"""The guardrails actually guard.

Before phase 3 these were wired to stubs: the price feed returned None for
every symbol and the risk data returned []. dd_30d([]) is 0.0, so the drawdown
halt could never fire however far equity fell, and the flash-crash check
compared None against None. Nothing here could pass before.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from intradyne.core.equity import EquityHistory
from intradyne.core.ledger import Ledger
from intradyne.core.marks import MarkStore
from intradyne.risk.guardrails import Guardrails, OrderReq, PriceFeed, RiskData
from intradyne.risk.shariah import ShariahPolicy


class _MarkFeed(PriceFeed):
    def __init__(self, marks: MarkStore):
        self._m = marks

    def get_price(self, symbol, at=None):
        return self._m.get(symbol, at)


class _HistoryData(RiskData):
    def __init__(self, history: EquityHistory):
        self._h = history

    def equity_series_30d(self):
        return self._h.series_30d()

    def equity_daily_returns_30d(self):
        return self._h.daily_returns(30)


def _build(tmp_path, marks=None, history=None, **thresholds):
    marks = marks if marks is not None else MarkStore()
    history = (
        history
        if history is not None
        else EquityHistory(f"sqlite:///{tmp_path / 'eq.sqlite'}")
    )
    gr = Guardrails(
        price_feed=_MarkFeed(marks),
        risk_data=_HistoryData(history),
        ledger=Ledger(path=str(tmp_path / "ledger.jsonl")),
        shariah=ShariahPolicy(allowed_crypto=["BTC/USDT"]),
        thresholds=thresholds or None,
    )
    return gr, marks, history


def _buy():
    return OrderReq(symbol="BTC/USDT", side="buy", qty=1.0)


# ---- drawdown ------------------------------------------------------------


def test_a_25_percent_drawdown_halts_trading(tmp_path):
    gr, _, history = _build(tmp_path)
    now = datetime.utcnow()
    for days_ago, equity in ((10, 10_000.0), (5, 9_000.0), (1, 7_500.0)):
        history.record(equity, ts=now - timedelta(days=days_ago))

    action, reasons, _ = gr.gate_trade(_buy())
    assert action == "halt"
    assert "drawdown" in reasons[0]


def test_a_small_drawdown_only_warns_and_still_allows(tmp_path):
    gr, _, history = _build(tmp_path)
    now = datetime.utcnow()
    for days_ago, equity in ((10, 10_000.0), (1, 8_300.0)):  # -17%, warn not halt
        history.record(equity, ts=now - timedelta(days=days_ago))

    action, reasons, _ = gr.gate_trade(_buy())
    assert action == "allow"
    assert any("dd_warn" in r for r in reasons)


def test_flat_equity_does_not_trip_anything(tmp_path):
    gr, _, history = _build(tmp_path)
    now = datetime.utcnow()
    for days_ago in range(10, 0, -1):
        history.record(10_000.0, ts=now - timedelta(days=days_ago))
    assert gr.gate_trade(_buy())[0] == "allow"


def test_drawdown_halt_is_recorded_and_the_chain_verifies(tmp_path):
    gr, _, history = _build(tmp_path)
    now = datetime.utcnow()
    history.record(10_000.0, ts=now - timedelta(days=5))
    history.record(7_000.0, ts=now - timedelta(days=1))

    gr.gate_trade(_buy())
    records = list(gr.ledger.iter_all())
    breaches = [r for r in records if r.get("event") == "guardrail_breach"]
    assert breaches and breaches[0]["type"] == "dd_halt"
    assert breaches[0]["action"] == "halt"
    assert gr.ledger.verify_chain()[0] is True


# ---- the durability property the whole design turns on -------------------


def test_drawdown_survives_a_restart(tmp_path):
    """With in-memory history a service that had just fallen 25% would come
    back believing its drawdown was 0.0 and resume trading."""
    db = f"sqlite:///{tmp_path / 'eq.sqlite'}"
    now = datetime.utcnow()

    first = EquityHistory(db)
    first.record(10_000.0, ts=now - timedelta(days=5))
    first.record(7_500.0, ts=now - timedelta(days=1))
    gr, _, _ = _build(tmp_path, history=first)
    assert gr.gate_trade(_buy())[0] == "halt"

    # A brand-new process: nothing in memory, same database file.
    reopened = EquityHistory(db)
    assert reopened.count() == 2
    gr2, _, _ = _build(tmp_path / "second", history=reopened)
    assert gr2.gate_trade(_buy())[0] == "halt", "halt disarmed across restart"


# ---- flash crash ---------------------------------------------------------


def test_a_35_percent_hourly_drop_pauses_trading(tmp_path):
    gr, marks, _ = _build(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    marks.record("BTC/USDT", 100.0, ts=now - 3600)
    marks.record("BTC/USDT", 65.0, ts=now)

    action, reasons, _ = gr.gate_trade(_buy())
    assert action == "pause"
    assert "flash_crash" in reasons[0]


def test_a_mild_hourly_drop_is_allowed(tmp_path):
    gr, marks, _ = _build(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    marks.record("BTC/USDT", 100.0, ts=now - 3600)
    marks.record("BTC/USDT", 95.0, ts=now)
    assert gr.gate_trade(_buy())[0] == "allow"


def test_flash_crash_pause_is_recorded(tmp_path):
    gr, marks, _ = _build(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    marks.record("BTC/USDT", 100.0, ts=now - 3600)
    marks.record("BTC/USDT", 50.0, ts=now)
    gr.gate_trade(_buy())
    breaches = [r for r in gr.ledger.iter_all() if r.get("event") == "guardrail_breach"]
    assert breaches[0]["type"] == "flash_crash"
    assert gr.ledger.verify_chain()[0] is True


def test_a_short_window_is_not_mistaken_for_an_hourly_move(tmp_path):
    """A store holding five minutes of data must not answer a 'price an hour
    ago' query with a five-minute-old price -- that would report a brief move
    as an hourly crash."""
    gr, marks, _ = _build(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    marks.record("BTC/USDT", 100.0, ts=now - 300)
    marks.record("BTC/USDT", 60.0, ts=now)
    assert gr.gate_trade(_buy())[0] == "allow"


# ---- VaR -----------------------------------------------------------------


def test_var_breach_steps_the_order_down(tmp_path):
    """The gate approves a smaller size rather than refusing outright.

    Drawdown thresholds are relaxed here so the VaR branch is reached: a
    return large enough to breach VaR is necessarily also a large drawdown,
    and dd_halt is checked first.
    """
    gr, _, history = _build(
        tmp_path, dd_warn=0.90, dd_halt=0.95, flash=0.90, kill_switch=99, var_max=0.05
    )
    now = datetime.utcnow()
    # Daily closes producing one large negative return, beyond var_max=0.05.
    equities = [10_000.0, 9_900.0, 9_800.0, 9_700.0, 9_600.0, 7_000.0]
    for i, eq in enumerate(equities):
        history.record(eq, ts=now - timedelta(days=len(equities) - i))

    action, reasons, adjusted = gr.gate_trade(_buy())
    assert action == "allow"
    assert any("var" in r for r in reasons)
    assert adjusted.qty < 1.0


# ---- kill switch ---------------------------------------------------------


def test_kill_switch_halts_after_repeated_breaches(tmp_path):
    gr, marks, _ = _build(tmp_path)
    now = datetime.now(timezone.utc).timestamp()
    marks.record("BTC/USDT", 100.0, ts=now - 3600)
    marks.record("BTC/USDT", 50.0, ts=now)

    seen = [gr.gate_trade(_buy())[0] for _ in range(5)]
    assert seen[0] == "pause"
    assert "halt" in seen, f"kill switch never engaged: {seen}"
