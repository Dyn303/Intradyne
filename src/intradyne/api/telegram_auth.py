"""Authenticate a Telegram Mini App request from its signed ``initData``.

A Mini App is a web page Telegram opens in its own browser, and Telegram hands
that page an ``initData`` query string signed with the bot token. The page
forwards it; this module checks the signature. The bot token stays in the
process and never reaches the browser, which makes this strictly better than
the ``X-API-Key`` scheme it sits beside -- that one requires the browser to
hold the credential.

The signature check is the easy half. The half that matters is *who* a valid
signature belongs to.

A Mini App has to be reachable over public HTTPS; Telegram will not open
localhost. So the moment this is switched on, the dashboard is on the open
internet, and a valid signature only proves the request came from *some*
Telegram user -- any of them, not the owner. Signature verification alone
would therefore authenticate the entire Telegram user base into a trading
console. That is why ``TELEGRAM_ALLOWED_USER_IDS`` is mandatory rather than
optional, and why an unset allowlist disables Mini App auth completely instead
of defaulting to open. Phase 2 of the plan puts halt and parameter controls
behind this door; the allowlist is what stands in front of them.

``auth_date`` is checked for freshness because ``initData`` is a bearer
credential in every practical sense: a static string that authenticates
whoever holds it. Telegram never expires one, so a copy captured from a log or
a browser history would work forever. The freshness window bounds that.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from urllib.parse import parse_qsl

#: Telegram's fixed HMAC salt. The signing key is HMAC(key="WebAppData",
#: msg=bot_token) -- the operands are in that order and not the other way
#: round, which is the usual way to get this wrong.
_SALT = b"WebAppData"

#: How old ``auth_date`` may be. Telegram itself suggests roughly a day; this
#: is shorter because the window is the entire lifetime of a stolen initData
#: string, and a Mini App session that outlives an afternoon is unusual.
DEFAULT_MAX_AGE_S = 3600.0

#: Tolerance for a client clock running ahead. Beyond this a timestamp is
#: either forged or the machine is badly wrong, and the freshness check is not
#: doing its job either way.
_FUTURE_SKEW_S = 300.0


class InitDataError(Exception):
    """initData was absent, malformed, unsigned, stale, or not on the list.

    Deliberately one exception type carrying a short machine-readable reason.
    Callers turn every one of them into the same flat 401: telling an
    unauthenticated caller *which* check failed tells an attacker whether they
    hold a genuine signature and merely the wrong user, which is the one thing
    worth not telling them. The reason is for the log, not the response.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TelegramUser:
    """The verified identity behind a request. Only what the API needs."""

    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None

    @property
    def label(self) -> str:
        """A short identifier safe to put in a log line."""
        return f"@{self.username}" if self.username else f"id:{self.id}"


# -- configuration ---------------------------------------------------------


def bot_token() -> str:
    """The signing token, shared with the outbound alerter."""
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def allowed_user_ids() -> FrozenSet[int]:
    """Who may use the Mini App. Empty means nobody, never everybody."""
    raw = (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").replace(";", ",")
    out: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            # A typo must not silently widen the allowlist, and must not crash
            # the app either. Drop the entry; `enabled()` reports the state
            # that results.
            continue
    return frozenset(out)


def max_age_s() -> float:
    try:
        return float(os.getenv("TELEGRAM_INITDATA_MAX_AGE_S", "") or DEFAULT_MAX_AGE_S)
    except ValueError:
        return DEFAULT_MAX_AGE_S


def enabled() -> bool:
    """Whether Mini App auth is available at all.

    Both halves are required. A token with no allowlist is the dangerous
    configuration -- it verifies signatures perfectly and admits everyone -- so
    it is reported as "not configured" rather than as "configured, open".
    """
    return bool(bot_token()) and bool(allowed_user_ids())


# -- verification ----------------------------------------------------------


def _data_check_string(pairs: List[Tuple[str, str]]) -> str:
    """Telegram's canonical form: every field except ``hash``, sorted by key.

    Only ``hash`` is removed. ``signature``, when present, stays in: it belongs
    to Telegram's separate Ed25519 scheme for third-party validation, but it
    was part of what Telegram hashed, so dropping it here would break the
    comparison for every recent client.
    """
    return "\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")


def verify_init_data(
    init_data: str,
    *,
    token: Optional[str] = None,
    allowed: Optional[FrozenSet[int]] = None,
    max_age: Optional[float] = None,
    now: Optional[float] = None,
) -> TelegramUser:
    """Verify one initData string and return the user it belongs to.

    Raises `InitDataError` on any failure. Every argument other than
    `init_data` exists so tests do not have to mutate process environment.
    """
    tok = bot_token() if token is None else token
    if not tok:
        raise InitDataError("not_configured")
    allow = allowed_user_ids() if allowed is None else allowed
    if not allow:
        raise InitDataError("no_allowlist")
    if not init_data:
        raise InitDataError("missing")

    # keep_blank_values matters: Telegram signed the string it sent, so a field
    # that arrived empty must still appear in the check string as "k=".
    pairs: List[Tuple[str, str]] = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    supplied = fields.get("hash", "")
    if not supplied:
        raise InitDataError("unsigned")

    secret = hmac.new(_SALT, tok.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret, _data_check_string(pairs).encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise InitDataError("bad_signature")

    # -- everything below here is trusted, because the signature covered it --

    window = max_age_s() if max_age is None else max_age
    if window > 0:
        try:
            issued = float(fields.get("auth_date", ""))
        except ValueError:
            raise InitDataError("no_auth_date") from None
        age = (time.time() if now is None else now) - issued
        if age > window:
            raise InitDataError("stale")
        if age < -_FUTURE_SKEW_S:
            raise InitDataError("future_dated")

    try:
        user: Dict[str, Any] = json.loads(fields.get("user", ""))
        uid = int(user["id"])
    except (ValueError, KeyError, TypeError):
        # Telegram omits the user object when the app is opened from an inline
        # context. There is then nobody to check against the allowlist, so
        # there is nobody to admit.
        raise InitDataError("no_user") from None

    if uid not in allow:
        raise InitDataError("not_allowed")

    return TelegramUser(
        id=uid,
        username=(user.get("username") or None),
        first_name=(user.get("first_name") or None),
    )


__all__ = [
    "DEFAULT_MAX_AGE_S",
    "InitDataError",
    "TelegramUser",
    "allowed_user_ids",
    "bot_token",
    "enabled",
    "max_age_s",
    "verify_init_data",
]
