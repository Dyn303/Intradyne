from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among `names`.

    Several settings are reachable under two names because the API and the
    engine were configured independently before they were merged. The first
    name listed is canonical.
    """
    for n in names:
        v = os.getenv(n)
        if v is not None and v.strip() != "":
            return v
    return default


def _f(*names: str, default: float) -> float:
    try:
        return float(_env(*names) or default)
    except ValueError:
        return default


def _i(*names: str, default: int) -> int:
    try:
        return int(_env(*names) or default)
    except ValueError:
        return default


def _b(*names: str, default: bool = False) -> bool:
    raw = _env(*names)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class RiskConfig(BaseModel):
    """Tier 2: position sizing and per-trade exits, applied inside the engine
    loop by RiskManager."""

    max_pos_pct: float = 0.015
    per_trade_sl_pct: float = 0.003
    tp_pct: float = 0.002
    max_concurrent_pos: int = 5
    # Session drawdown. Distinct from the 30-day peak-to-trough measure in
    # GuardrailConfig -- see MIGRATION.md phase 3.
    dd_soft: float = 0.03
    dd_hard: float = 0.05
    flash_crash_drop_1h: float = 0.30
    kill_switch_breaches: int = 3
    use_atr: bool = False
    atr_window: int = 14
    atr_k_sl: float = 1.5
    atr_k_tp: float = 2.0


class GuardrailConfig(BaseModel):
    """Tier 1: the pre-trade veto thresholds used by Guardrails.gate_trade."""

    # 30-day peak-to-trough drawdown, not the session drawdown above.
    dd_warn_pct: float = 0.15
    dd_halt_pct: float = 0.20
    flash_crash_pct: float = 0.30
    var_1d_max: float = 0.05
    kill_switch_breaches: int = 3

    # Exposure caps, in quote currency. 0 disables a cap.
    #
    # The risk guardrails bound drawdown and volatility but nothing bounded
    # how much the system could transact: a strategy looping on a bad signal
    # can place unlimited orders that are each individually small enough to
    # pass every threshold. These bound the total.
    max_order_notional: float = 0.0
    max_symbol_notional_24h: float = 0.0
    max_daily_notional: float = 0.0


class FeesConfig(BaseModel):
    maker_bps: int = 2
    taker_bps: int = 5
    slippage_bps: int = 2


class Settings(BaseSettings):
    """One configuration for the whole system.

    The API and the engine previously had separate Settings classes reading
    overlapping but differently-named environment variables, so setting
    FLASH_CRASH_PCT armed the API guardrail while leaving the engine's
    identical threshold at its default. Shared values are now read once and
    handed to both tiers.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.txt"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Mode and venue
    mode: str = "paper"  # paper | live
    exchange: str = "bitget"
    use_testnet: bool = True
    live_trading_enabled: bool = False

    # Broker credentials. API_KEY / API_SECRET / API_PASSPHRASE are accepted
    # as legacy aliases. These are the *broker* credentials; the HTTP API key
    # is X_API_KEY and is deliberately unrelated.
    bitget_api_key: Optional[str] = None
    bitget_api_secret: Optional[str] = None
    bitget_api_passphrase: Optional[str] = None
    ccxt_exchange_id: Optional[str] = None
    ccxt_api_key: Optional[str] = None
    ccxt_secret: Optional[str] = None

    # Paths and infrastructure
    port: int = 8000
    log_dir: str = "logs"
    log_level: str = "INFO"
    data_dir: str = "data"
    artifacts_dir: str = "artifacts"
    optuna_db_url: str = "sqlite:///optuna.db"
    db_url: str = "sqlite:///data/trades.sqlite"
    redis_url: Optional[str] = None
    explain_ledger_path: str = "explainability_ledger.jsonl"

    # Universe. Accepts either BASE or BASE/QUOTE.
    allowed_symbols: str = "BTC,ETH,SOL,XRP,ADA,LTC,AVAX,DOT,MATIC,USDT"
    symbols: List[str] = []

    # HTTP rate limits
    rate_limit_window: int = 60
    rate_limit_reqs: int = 120
    ai_rate_limit_window: Optional[int] = None
    ai_rate_limit_reqs: Optional[int] = None

    # Trading loop. Off by default: phase 2 puts the machinery in place and
    # switching it on is a separate, deliberate step.
    engine_enabled: bool = False

    # Execution style.
    #
    # "taker" crosses the spread on every order: immediate fills, but 5+2bps a
    # side, and at ETH's volatility that round trip exceeds the entire move
    # expected over a two-minute hold. "maker" posts passively and pays 2bps
    # with no slippage, at the cost of orders that do not fill.
    execution_mode: str = "taker"  # taker | maker
    #: How far inside the touch to post, in bps. 0 joins the bid/ask.
    maker_offset_bps: float = 0.0
    #: How long a resting order waits before being cancelled.
    limit_ttl_s: float = 60.0

    # Execution filters
    #: Refuse entries when the touch spread is wider than this, in bps.
    #:
    #: This defaulted to 0 -- disabled -- which made it a fail-open filter:
    #: the venue could quote any spread it liked and the engine would cross
    #: it. Measured on Bitget across the traded whitelist, the spreads that
    #: default admitted were not hypothetical:
    #:
    #:     BTC 0.00   ETH 0.04   SOL 0.96   XRP 0.69
    #:     LTC 1.96   AVAX 1.33  ADA 4.51   DOT 11.38-22.78
    #:
    #: DOT had *nothing* resting within 5bps of the touch.
    #:
    #: An earlier version of this comment justified the bound as "2x
    #: `slippage_bps`, to keep reality inside the cost model's assumption".
    #: That reasoning was wrong and is corrected here. `PaperBroker._try_fill`
    #: already fills at the touch -- `px = ask if buy else bid` -- so the
    #: model was never assuming a flat spread; `slippage_bps` is an extra
    #: impact term *on top of* crossing the real one. Measured round trips:
    #: BTC 14.00bps, LTC 15.96, ADA 18.51, DOT 25.38. Cost tracked the quoted
    #: spread 1:1 all along, and DOT was charged the most, not the least.
    #:
    #: So the filter is not defending the cost model. It is an economic
    #: bound: round trip is `spread + 4bps slippage + 10bps taker`, so a
    #: bound of 4 caps the worst round trip at 18bps instead of DOT's 25.38.
    #: It separates the six liquid names from the two thin ones.
    #:
    #: Stated plainly, because a threshold invites the wrong inference: this
    #: does not make anything profitable. Against an edge measured at ~0.5bps
    #: every admitted name still loses. The bound limits how fast, and keeps
    #: the traded universe to instruments whose cost is at least measurable.
    max_spread_bps: int = 4  # 0 disables
    #: Fallback smallest order, in quote currency, for symbols whose venue
    #: declares no `limits.cost.min`. The venue's own figure is preferred and
    #: overrides this at runtime; Bitget reports $1.00 across the whitelist.
    min_order_notional: float = 1.0
    #: Smallest *entry* worth placing, in quote currency. The venue minimum
    #: above says what the exchange rejects; this says what is not worth an
    #: order slot, and applies to buys only so an exit is never blocked.
    #:
    #: 5% of MAX_ORDER_NOTIONAL (300), so a remnant worth less than a
    #: twentieth of a full order is skipped. Observed before this existed:
    #: 3 of 288 fills in an hour were $1.01, $1.13 and $1.22 -- valid, and
    #: pointless. 0 disables it.
    min_entry_notional: float = 15.0
    #: Stage 2 control arm. Per-tick probability that the control strategy
    #: signals a buy, ignoring the market. 0 disables it and the real
    #: strategies run. See docs/STAGE_2_PREREGISTRATION.md.
    random_entry_p: float = 0.0
    #: Seed for that control, so a run can be repeated.
    random_entry_seed: int = 0
    #: The spread a backtest prices its fills against, in bps. OHLCV carries
    #: no spread, so one has to be assumed, and the assumption decides what a
    #: backtest concludes. This was hardcoded at 1.0 inside `bars_to_l1` and
    #: never passed, so every instrument was modelled at one basis point --
    #: near enough on the liquid names and 10bps optimistic on DOT.
    #:
    #: The default is `max_spread_bps`: the live filter refuses anything
    #: wider, so the widest spread the system will actually trade is the
    #: conservative reading of what a fill could have cost. Override per
    #: instrument with real measurements where they exist.
    backtest_spread_bps: float = 4.0
    entry_cooldown_s: int = 0

    # Sentiment
    sentiment_enabled: bool = False
    sentiment_long_min: float = 0.0
    sentiment_size_min: float = 0.8
    sentiment_size_max: float = 1.2
    sentiment_smooth_n: int = 12

    risk: RiskConfig = RiskConfig()
    guardrails: GuardrailConfig = GuardrailConfig()
    fees: FeesConfig = FeesConfig()

    # ---- derived -------------------------------------------------------

    def allowed_crypto_list(self) -> List[str]:
        raw = [s.strip() for s in (self.allowed_symbols or "").split(",") if s.strip()]
        out: List[str] = []
        for s in raw:
            if "/" in s:
                try:
                    base, quote = s.split("/", 1)
                except ValueError:
                    continue
                if base.upper() == quote.upper():
                    continue
                out.append(f"{base}/{quote}")
            else:
                if s.upper() == "USDT":
                    continue
                out.append(f"{s}/USDT")
        return out

    def compliance_universe(self) -> List[str]:
        """Every instrument the Shariah screen permits, from `whitelist.json`.

        A **ceiling**, not a trading list. Nothing may be traded that is absent
        here, but presence is permission rather than intent -- which of these
        to actually trade is `allowed_crypto_list()`.
        """
        whitelist_path = (
            Path(__file__).resolve().parent.parent / "engine" / "whitelist.json"
        )
        with open(whitelist_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        return list(wl.get("symbols", []))

    def load_symbols(self, markets: Optional[List[str]] = None) -> List[str]:
        """The instruments that may actually be traded.

        Two lists used to feed two order paths independently, and they
        disagreed. `whitelist.json` carried 15 pairs and drove the live loop
        (`engine/loop.py`) and the backtester; `ALLOWED_SYMBOLS` carried 9 and
        drove the API's `ExecutionManager` (`api/deps.py`). LINK, XLM, ATOM,
        TRX, NEAR and ALGO were therefore tradeable by the loop and refused by
        the API -- the stricter list did not govern the path that places
        orders, which is the wrong way round for a fail-open to run.

        They are not redundant, which is why the fix is not to delete one. The
        whitelist is a *compliance* artifact saying what is permissible;
        `ALLOWED_SYMBOLS` is *operator configuration* saying what to trade
        today. The defect was that neither constrained the other. The effective
        universe is now their intersection, so the compliance list is a ceiling
        the operator cannot raise and the operator list is a selection within
        it.

        An operator entry absent from the compliance list is a configuration
        error, not a silent no-op: it means someone tried to enable an
        unscreened instrument, and it is logged rather than dropped quietly.
        """
        permitted = self.compliance_universe()
        selected = self.allowed_crypto_list()

        if selected:
            # Matched case-insensitively. An operator writing `btc/usdt` means
            # the same instrument as `BTC/USDT`, and dropping it for the case
            # would be a silent refusal indistinguishable from a compliance
            # one -- the two must not look alike.
            by_upper = {s.upper(): s for s in permitted}
            chosen = {by_upper[s.upper()] for s in selected if s.upper() in by_upper}
            unscreened = [s for s in selected if s.upper() not in by_upper]
            if unscreened:
                logger.bind(event="unscreened_symbols_ignored").warning(
                    "ALLOWED_SYMBOLS names instruments absent from the Shariah "
                    f"whitelist and they will not be traded: {sorted(unscreened)}. "
                    "Add them to engine/whitelist.json if they have been screened."
                )
            syms = [s for s in permitted if s in chosen]
        else:
            # No operator selection configured: the compliance list stands
            # alone, which is its existing meaning.
            syms = list(permitted)

        if markets:
            # Narrowing to what the venue lists, and saying which names it
            # removed. This dropped them silently, which is how MATIC/USDT sat
            # in the whitelist unnoticed after Polygon migrated the token to
            # POL in 2024: permissible, configured, and not a listed ticker.
            # A delisting is a fact about the world that should reach a human,
            # not a set difference computed at startup and discarded -- the
            # same reasoning that already logs unscreened operator entries.
            unlisted = [s for s in syms if s not in markets]
            if unlisted:
                logger.bind(event="unlisted_symbols_dropped").warning(
                    f"{self.exchange} does not list {sorted(unlisted)}; they are "
                    "permitted and selected but cannot be traded. Check for a "
                    "ticker migration or a delisting."
                )
            syms = [s for s in syms if s in markets]
        self.symbols = syms
        return self.symbols

    # ---- validation ----------------------------------------------------

    def _map_compat(self) -> None:
        """Fill BITGET_* from CCXT_* when the venue is bitget."""
        if (self.ccxt_exchange_id or "").lower() != "bitget":
            return
        if (
            not (self.bitget_api_key or "").strip()
            and (self.ccxt_api_key or "").strip()
        ):
            self.bitget_api_key = self.ccxt_api_key
        if (
            not (self.bitget_api_secret or "").strip()
            and (self.ccxt_secret or "").strip()
        ):
            self.bitget_api_secret = self.ccxt_secret

    def _validate_required_in_prod(self) -> None:
        env = _env("APP_ENV", "ENV", "ENVIRONMENT").lower()
        if env not in {"prod", "production"}:
            return
        missing = [
            name
            for name, val in (
                ("BITGET_API_KEY", self.bitget_api_key),
                ("BITGET_API_SECRET", self.bitget_api_secret),
                ("BITGET_API_PASSPHRASE", self.bitget_api_passphrase),
            )
            if not (val or "").strip()
        ]
        if missing:
            raise RuntimeError(
                f"Missing required credentials in production: {', '.join(missing)}"
            )


# Phase 5 of MIGRATION.md opens live trading. The controls it required are
# built; what remains is operational validation that cannot be done from a
# development machine, plus an edge. See RUNBOOK section 8.
LIVE_TRADING_GATE_OPEN = False


def assert_live_trading_gate(settings: "Settings") -> None:
    """Refuse to start with live trading armed before phase 5 is done.

    Deliberately not overridable by an environment variable: an env override
    is exactly how this would get flipped by accident. Opening it is a code
    change to LIVE_TRADING_GATE_OPEN, which leaves a reviewable commit.

    The message below used to name four controls as missing -- idempotency,
    reconciliation, notional caps and halt alerting -- and all four had since
    been built, wired, and covered by `tests/test_live_readiness.py`. RUNBOOK
    section 8 had been updated; this had not. An operator reading it would
    either distrust a system further along than it claimed, or go and build a
    second copy of working machinery. A control that misinforms is the failure
    this project keeps finding, and this one sat in the message an operator
    sees at exactly the moment they try to go live.

    `tests/test_live_gate_message.py` now ties each claim to the code, so the
    message cannot drift from reality again without a test failing.
    """
    if LIVE_TRADING_GATE_OPEN:
        return
    if settings.mode == "live" and settings.live_trading_enabled:
        raise RuntimeError(
            "Live trading is armed (MODE=live and LIVE_TRADING_ENABLED=true) "
            "but the gate is shut.\n\n"
            "The phase 5 controls are built: idempotency claimed before the "
            "venue is contacted (core/idempotency.py), restart reconciliation "
            "that halts rather than guessing (engine/reconcile.py), per-order, "
            "per-symbol and daily notional caps that fail closed "
            "(core/limits.py), and alerting on halt (core/alerts.py).\n\n"
            "What remains cannot be done from a development machine: a testnet "
            "soak, confirming a page actually reaches a human, rehearsing the "
            "halt under live conditions, and setting the exposure caps -- which "
            "default to 0, meaning disabled, so arming live without configuring "
            "them leaves transacted volume bounded only by the risk "
            "thresholds.\n\n"
            "And no edge has been demonstrated: STRATEGY_EDGE_DEMONSTRATED is "
            "False. See RUNBOOK section 8. Run with MODE=paper."
        )


# Measured across 50 signals, 8 families, 943 days and two instruments: the
# best entry signal is worth ~0.5bps against a round-trip cost of 4-14bps.
# See the "Fifty signals" and "Months of data" sections of MIGRATION.md.
#
# Flipping this to True is a claim that an edge has been demonstrated, and
# should come with the measurement that demonstrates it.
STRATEGY_EDGE_DEMONSTRATED = False

#: Env var that lets research proceed anyway, knowingly.
ACKNOWLEDGE_NO_EDGE = "ACKNOWLEDGE_NO_EDGE"


def assert_strategy_edge_gate(settings: "Settings") -> None:
    """Refuse to start the trading loop on a strategy known to lose money.

    The engine would otherwise happily paper-trade the shipped strategy,
    which measurement puts at roughly -13bps per round trip. A result that
    lives only in a markdown file is one `git pull` away from being
    forgotten, so it is enforced here instead.

    Unlike the live gate this *is* overridable, because paper trading is how
    a replacement strategy would be validated -- refusing outright would
    block the only legitimate path forward. The override is deliberately
    explicit and noisy rather than a quiet default.
    """
    if STRATEGY_EDGE_DEMONSTRATED or not settings.engine_enabled:
        return
    if _b(ACKNOWLEDGE_NO_EDGE):
        logger.bind(event="no_edge_acknowledged").warning(
            "Trading loop started on a strategy with no demonstrated edge "
            f"({ACKNOWLEDGE_NO_EDGE} is set). Measured at roughly -13bps per "
            "round trip. Paper mode only unless you know exactly why."
        )
        return
    raise RuntimeError(
        "Refusing to start the trading loop: no edge has been demonstrated. "
        "The best of 50 signals measured over 943 days and two instruments is "
        "worth ~0.5bps against a 4-14bps round trip, so this strategy loses "
        f"roughly 13bps per trade. Set {ACKNOWLEDGE_NO_EDGE}=true to run it "
        "anyway for research, set ENGINE_ENABLED=false to run the API without "
        "the loop, or demonstrate an edge and flip "
        "STRATEGY_EDGE_DEMONSTRATED. See MIGRATION.md."
    )


def _build_settings() -> Settings:
    # Shared thresholds are read once here and handed to both tiers, so one
    # environment variable cannot arm one tier and leave the other at default.
    flash = _f("FLASH_CRASH_PCT", "FLASH_CRASH_DROP_1H", default=0.30)
    kill = _i("KILL_SWITCH_BREACHES", default=3)

    s = Settings(
        bitget_api_key=_env("BITGET_API_KEY", "API_KEY") or None,
        bitget_api_secret=_env("BITGET_API_SECRET", "API_SECRET") or None,
        bitget_api_passphrase=_env("BITGET_API_PASSPHRASE", "API_PASSPHRASE") or None,
        sentiment_enabled=_b("SENTIMENT_ENABLED", "SENTIMENT_ENABLE"),
        risk=RiskConfig(
            max_pos_pct=_f("MAX_POS_PCT", default=0.015),
            per_trade_sl_pct=_f("PER_TRADE_SL_PCT", default=0.003),
            tp_pct=_f("TP_PCT", default=0.002),
            max_concurrent_pos=_i("MAX_CONCURRENT_POS", default=5),
            dd_soft=_f("DD_SOFT", default=0.03),
            dd_hard=_f("DD_HARD", default=0.05),
            flash_crash_drop_1h=flash,
            kill_switch_breaches=kill,
            use_atr=_b("USE_ATR"),
            atr_window=_i("ATR_WINDOW", default=14),
            atr_k_sl=_f("ATR_K_SL", default=1.5),
            atr_k_tp=_f("ATR_K_TP", default=2.0),
        ),
        guardrails=GuardrailConfig(
            dd_warn_pct=_f("DD_WARN_PCT", default=0.15),
            dd_halt_pct=_f("DD_HALT_PCT", default=0.20),
            flash_crash_pct=flash,
            var_1d_max=_f("VAR_1D_MAX", default=0.05),
            kill_switch_breaches=kill,
            max_order_notional=_f("MAX_ORDER_NOTIONAL", default=0.0),
            max_symbol_notional_24h=_f("MAX_SYMBOL_NOTIONAL_24H", default=0.0),
            max_daily_notional=_f("MAX_DAILY_NOTIONAL", default=0.0),
        ),
        fees=FeesConfig(
            maker_bps=_i("MAKER_BPS", default=2),
            taker_bps=_i("TAKER_BPS", default=5),
            slippage_bps=_i("SLIPPAGE_BPS", default=2),
        ),
    )
    s._map_compat()
    s._validate_required_in_prod()
    return s


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return the process-wide Settings, built once.

    Called on every request by the rate limiters and by several routes.
    Uncached, each call re-parsed the .env files and re-ran the production
    credential check, so a prod deployment without broker credentials raised
    on every request -- /healthz included -- even though the API places no
    orders itself.

    Tests that manipulate the environment must call reset_settings_cache();
    tests/conftest.py does so automatically between tests.
    """
    return _build_settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings so the next load re-reads the environment."""
    load_settings.cache_clear()


__all__ = [
    "Settings",
    "assert_live_trading_gate",
    "assert_strategy_edge_gate",
    "LIVE_TRADING_GATE_OPEN",
    "RiskConfig",
    "GuardrailConfig",
    "FeesConfig",
    "load_settings",
    "reset_settings_cache",
]
