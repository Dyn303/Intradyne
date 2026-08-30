"""Standalone entrypoint for the trading engine.

This used to build a second copy of the whole system -- its own Portfolio,
PaperBroker, ExplainabilityLedger and ExecutionManager, plus its own FastAPI
app -- and run the loop against that. So `python -m intradyne.engine.main` and
the API served different portfolios and wrote different ledgers, which is the
split-brain the consolidation set out to end.

It is now a thin wrapper: it turns the engine on and serves the one canonical
application, which hosts the loop in its lifespan against the shared execution
path. Engine status and runtime reconfiguration live at /engine/* on that app;
they were previously on the separate server this module used to start.
"""

from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv

from intradyne.core.config import assert_live_trading_gate, load_settings


def main() -> None:
    load_dotenv()
    # Enable the loop before Settings is read, so the app's lifespan starts it.
    os.environ["ENGINE_ENABLED"] = "true"

    settings = load_settings()
    assert_live_trading_gate(settings)

    uvicorn.run(
        "intradyne.api.app:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
