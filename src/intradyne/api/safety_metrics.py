"""Prometheus gauges for the trading system's own safety state.

The alert rules covered infrastructure only -- API up, scrape duration, nginx
5xx -- so nothing would have paged when the system halted itself, when
drawdown approached the halt threshold, or when live orders were left
unreconciled. Those are the conditions an operator most needs to hear about.

Values are refreshed at scrape time by the /metrics handler rather than
maintained at every state change, so a gauge cannot go stale by way of some
code path forgetting to update it.
"""

from __future__ import annotations

from prometheus_client import Gauge

from intradyne.risk.kill_switch import is_halted


HALTED = Gauge("intradyne_halted", "1 when trading is halted, 0 otherwise")
LIVE_ARMED = Gauge(
    "intradyne_live_trading_armed",
    "1 when MODE=live and LIVE_TRADING_ENABLED=true",
)
DRAWDOWN_30D = Gauge("intradyne_drawdown_30d", "30-day peak-to-trough drawdown")
VAR_1D = Gauge("intradyne_var_1d", "1-day historical VaR at 95%")
EQUITY = Gauge("intradyne_equity", "Latest recorded portfolio equity")
BREACHES_24H = Gauge(
    "intradyne_guardrail_breaches_24h", "Guardrail breaches in the last 24h"
)
UNRECONCILED = Gauge(
    "intradyne_unreconciled_orders",
    "Live submissions claimed but never completed",
)


def refresh() -> None:
    """Update every gauge from current state. Never raises.

    Called from the /metrics handler, so a failure here must not take the
    scrape endpoint down with it -- a monitoring gap is bad, but losing all
    metrics because one of them could not be computed is worse.
    """
    try:
        HALTED.set(1 if is_halted() else 0)
    except Exception:  # noqa: BLE001
        pass

    try:
        from intradyne.core.config import load_settings

        settings = load_settings()
        LIVE_ARMED.set(
            1 if (settings.mode == "live" and settings.live_trading_enabled) else 0
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        from datetime import datetime, timedelta

        from intradyne.api.deps import get_guardrails
        from intradyne.risk.guardrails import dd_30d, historical_var

        gr = get_guardrails()
        series = gr.risk.equity_series_30d()
        DRAWDOWN_30D.set(dd_30d(series))
        VAR_1D.set(historical_var(gr.risk.equity_daily_returns_30d(), alpha=0.95))
        if series:
            EQUITY.set(series[-1][1])
        since = datetime.utcnow() - timedelta(hours=24)
        BREACHES_24H.set(
            sum(
                1
                for r in gr.ledger.iter_recent(since)
                if r.get("event") == "guardrail_breach"
            )
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        from intradyne.core.config import load_settings
        from intradyne.core.idempotency import OrderKeyStore
        from intradyne.engine.reconcile import find_unreconciled

        UNRECONCILED.set(len(find_unreconciled(OrderKeyStore(load_settings().db_url))))
    except Exception:  # noqa: BLE001
        pass


__all__ = ["refresh"]
