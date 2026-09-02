"""Outbound alerts to Telegram.

Outbound only, which is what makes this the cheapest useful piece of the
full-stack work: the process calls ``api.telegram.org`` and nothing needs a
public URL, a tunnel, or an open port. A dashboard only helps when someone is
already looking at it; a halt at 3am does not wait for that.

Three properties matter more than the feature itself.

**A failed alert must never affect trading.** Every send is wrapped and
swallowed. A network failure, a revoked token, Telegram being down -- none of
it may propagate into the order path. The alert is a notification about the
system, not part of it.

**Secrets stay out of the message.** Alert bodies are built from event names
and numbers, and the token lives only in the environment. ``redact_secrets``
is applied to any payload dict before formatting, because ledger records and
guardrail reasons can carry configuration values.

**Silence is the default.** With no token configured the sender is disabled
and every call is a no-op, so the system behaves identically whether or not
anyone set it up.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import httpx
from loguru import logger

from intradyne.core.logging import redact_secrets

TELEGRAM_API = "https://api.telegram.org"

#: Alerts worth waking someone for. Anything not listed is logged, not sent.
CRITICAL_EVENTS = frozenset(
    {
        "halt_engaged",
        "kill_switch_fired",
        "drawdown_breach",
        "reconcile_mismatch",
        "engine_crashed",
    }
)


@dataclass
class AlertConfig:
    token: Optional[str] = None
    chat_id: Optional[str] = None
    #: Minimum seconds between identical alerts. A guardrail that trips on
    #: every tick would otherwise send hundreds of messages a minute and get
    #: the bot rate-limited into uselessness.
    cooldown_s: float = 300.0
    timeout_s: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)


def config_from_env() -> AlertConfig:
    return AlertConfig(
        token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip() or None,
        chat_id=(os.getenv("TELEGRAM_CHAT_ID") or "").strip() or None,
        cooldown_s=float(os.getenv("TELEGRAM_COOLDOWN_S", "300")),
    )


@dataclass
class Alerter:
    """Sends critical events to Telegram, or silently does nothing."""

    cfg: AlertConfig = field(default_factory=config_from_env)
    _last_sent: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- formatting -------------------------------------------------------

    @staticmethod
    def format(event: str, payload: Optional[Mapping[str, Any]] = None) -> str:
        """Build the message body, with secrets stripped.

        Guardrail reasons and ledger records can carry configuration values,
        so the payload goes through the same redaction the logs use rather
        than being trusted because it came from inside the process.
        """
        safe: Dict[str, Any] = {}
        if payload:
            red = redact_secrets(dict(payload))
            if isinstance(red, dict):
                safe = red
        lines = [f"[intradyne] {event}"]
        for k, v in safe.items():
            if isinstance(v, float):
                lines.append(f"  {k}: {v:,.6g}")
            else:
                text = str(v)
                lines.append(f"  {k}: {text[:200]}")
        return "\n".join(lines)

    # -- rate limiting ----------------------------------------------------

    def _should_send(self, key: str, now: float) -> bool:
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and now - last < self.cfg.cooldown_s:
                return False
            self._last_sent[key] = now
            return True

    # -- sending ----------------------------------------------------------

    def send(
        self,
        event: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        force: bool = False,
    ) -> bool:
        """Send one alert. Returns whether it was delivered.

        Never raises. A failure here must not touch the order path, so every
        outcome -- disabled, rate-limited, network error, API rejection --
        returns False and logs rather than propagating.
        """
        if not self.cfg.enabled:
            return False
        if not force and event not in CRITICAL_EVENTS:
            return False
        if not self._should_send(event, time.time()):
            logger.bind(event="alert_suppressed").debug(
                {"alert": event, "reason": "cooldown"}
            )
            return False

        body = self.format(event, payload)
        try:
            r = httpx.post(
                f"{TELEGRAM_API}/bot{self.cfg.token}/sendMessage",
                json={"chat_id": self.cfg.chat_id, "text": body},
                timeout=self.cfg.timeout_s,
            )
            if r.status_code != 200:
                # The token is in the URL, so the URL never goes in the log.
                logger.bind(event="alert_failed").warning(
                    {"alert": event, "status": r.status_code}
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.bind(event="alert_failed").warning(
                {"alert": event, "error": type(exc).__name__}
            )
            return False


_ALERTER: Optional[Alerter] = None
_ALERTER_LOCK = threading.Lock()


def get_alerter() -> Alerter:
    """Process-wide alerter, built once from the environment."""
    global _ALERTER
    with _ALERTER_LOCK:
        if _ALERTER is None:
            _ALERTER = Alerter()
            logger.bind(event="alerts_configured").info(
                {"enabled": _ALERTER.cfg.enabled}
            )
        return _ALERTER


def reset_alerter() -> None:
    """Drop the cached alerter. For tests and config reloads."""
    global _ALERTER
    with _ALERTER_LOCK:
        _ALERTER = None


def alert(event: str, payload: Optional[Mapping[str, Any]] = None, **kw) -> bool:
    """Fire an alert. Safe to call from anywhere, including the order path."""
    try:
        return get_alerter().send(event, payload, **kw)
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "CRITICAL_EVENTS",
    "AlertConfig",
    "Alerter",
    "alert",
    "config_from_env",
    "get_alerter",
    "reset_alerter",
]
