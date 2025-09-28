from __future__ import annotations

from typing import Deque, Dict, List, Optional, Tuple
from collections import defaultdict, deque

from loguru import logger

from .execution import ExecutionManager
from .risk import RiskManager
from .portfolio import Portfolio
from .strategies.momentum import MomentumStrategy
from .strategies.meanrev import MeanRevStrategy
from .strategies.ml import MLStrategy
from .metrics_ml import ML_SIGNALS
from .metrics import METRICS


class StrategyRouter:
    def __init__(
        self,
        symbols: List[str],
        risk: RiskManager,
        execman: ExecutionManager,
        portfolio: Portfolio,
        params: Dict[str, Dict[str, float]] | None = None,
    ) -> None:
        self.symbols = symbols
        self.risk = risk
        self.execman = execman
        self.portfolio = portfolio
        self.momo = {s: MomentumStrategy(symbol=s) for s in symbols}
        self.meanrev = {s: MeanRevStrategy(symbol=s) for s in symbols}
        self.ml: dict[str, MLStrategy] = {}

        # Position/entry state
        self.stops: dict[str, tuple[float, float]] = {}
        self.entry_ts: dict[str, float] = {}
        self._entry_high: Dict[str, float] = {}
        self._entry_price: Dict[str, float] = {}
        self._mfe_pct: Dict[str, float] = defaultdict(float)
        self._mae_pct: Dict[str, float] = defaultdict(float)

        # Execution controls
        self.micro_slices: int = 3
        self.time_stop_s: int = 120
        self.trail_atr_k: float = 0.0
        self.pyramid_max: int = 0
        self.pyramid_step_pct: float = 0.0
        self._pyramids_done: Dict[str, int] = defaultdict(int)
        self.partial_r1: float = 0.0
        self.partial_r2: float = 0.0
        self._partial_stage: Dict[str, int] = defaultdict(int)

        # ATR and EMA tracking
        self._atr_window: int = int(risk.atr_window or 0)
        self._ohlc: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(
            lambda: deque(maxlen=max(2, self._atr_window + 1))
        )
        self.ema_fast_n: int = 0
        self.ema_slow_n: int = 0
        self._ema_fast: Dict[str, float] = {}
        self._ema_slow: Dict[str, float] = {}
        self.min_atr_pct: float = 0.0
        self.max_atr_pct: float = 0.0
        self.atr_block_consec: int = 0
        self.atr_block_cooldown_s: int = 0
        self._atr_out_count: Dict[str, int] = defaultdict(int)
        self._atr_block_until: Dict[str, float] = defaultdict(float)

        # Apply parameter overrides if provided
        params = params or {}
        for s in symbols:
            m = self.momo[s]
            for k, v in params.get("momentum", {}).items():
                if hasattr(m, k):
                    setattr(m, k, v)  # type: ignore
            r = self.meanrev[s]
            for k, v in params.get("meanrev", {}).items():
                if hasattr(r, k):
                    setattr(r, k, v)  # type: ignore

        # ML strategy (optional)
        ml_params = params.get("ml", {}) if isinstance(params, dict) else {}
        if ml_params and bool(ml_params.get("enabled", False)):
            model_path = str(
                ml_params.get("model_path", "artifacts/models/ml_pipeline.joblib")
            )
            prob_cut = float(ml_params.get("prob_cut", 0.6))
            for s in symbols:
                self.ml[s] = MLStrategy(
                    symbol=s, model_path=model_path, prob_cut=prob_cut
                )

        # Execution-level knobs
        ex_params = params.get("execution", {})
        if isinstance(ex_params, dict):
            self.micro_slices = int(ex_params.get("micro_slices", self.micro_slices))
            self.time_stop_s = int(ex_params.get("time_stop_s", self.time_stop_s))
            self.trail_atr_k = float(ex_params.get("trail_atr_k", self.trail_atr_k))
            self.pyramid_max = int(ex_params.get("pyramid_max", self.pyramid_max))
            self.pyramid_step_pct = float(
                ex_params.get("pyramid_step_pct", self.pyramid_step_pct)
            )
            self.partial_r1 = float(ex_params.get("partial_r1", self.partial_r1))
            self.partial_r2 = float(ex_params.get("partial_r2", self.partial_r2))

        # Filters
        flt = params.get("filters", {}) if isinstance(params, dict) else {}
        if isinstance(flt, dict):
            self.ema_fast_n = int(flt.get("ema_fast", self.ema_fast_n))
            self.ema_slow_n = int(flt.get("ema_slow", self.ema_slow_n))
            self.min_atr_pct = float(flt.get("min_atr_pct", self.min_atr_pct))
            self.max_atr_pct = float(flt.get("max_atr_pct", self.max_atr_pct))
            self.atr_block_consec = int(
                flt.get("atr_block_consec", self.atr_block_consec)
            )
            self.atr_block_cooldown_s = int(
                flt.get("atr_block_cooldown_s", self.atr_block_cooldown_s)
            )

    async def on_tick(self, l1: Dict[str, object]) -> None:
        sym = l1["symbol"]  # type: ignore[index]
        last = l1.get("last") or l1.get("bid") or l1.get("ask")
        if last is None:
            return
        last_f = float(last)
        now_ts = float(l1.get("ts", 0.0) or 0.0)

        # Update EMAs
        if self.ema_fast_n > 1:
            kf = 2.0 / (self.ema_fast_n + 1.0)
            self._ema_fast[sym] = (
                last_f
                if sym not in self._ema_fast
                else last_f * kf + self._ema_fast[sym] * (1.0 - kf)
            )
        if self.ema_slow_n > 1:
            ks = 2.0 / (self.ema_slow_n + 1.0)
            self._ema_slow[sym] = (
                last_f
                if sym not in self._ema_slow
                else last_f * ks + self._ema_slow[sym] * (1.0 - ks)
            )

        # Update ATR buffers
        if self._atr_window > 0:
            try:
                hi = float(l1.get("high", last_f))
                lo = float(l1.get("low", last_f))
                cl = float(l1.get("last", last_f))
                self._ohlc[sym].append((hi, lo, cl))
            except Exception:
                pass

        # Risk shield
        if self.risk.flash_crash_check(sym, now_ts, last_f):
            logger.warning(f"Flash-crash shield halted {sym}")
            return

        # Position state
        pos = self.portfolio.get_position(sym)
        if pos.base > 0 and sym in self._entry_price:
            ep = self._entry_price.get(sym, last_f)
            if ep > 0:
                dp = (last_f / ep) - 1.0
                self._mfe_pct[sym] = max(self._mfe_pct.get(sym, 0.0), dp)
                self._mae_pct[sym] = min(self._mae_pct.get(sym, 0.0), dp)

        # Handle exits for open position
        if pos.base > 0 and sym in self.stops:
            sl, tp = self.stops[sym]
            # Trailing stop update
            try:
                self._entry_high[sym] = max(self._entry_high.get(sym, 0.0), last_f)
                if self.trail_atr_k > 0:
                    atr_val = self._compute_atr(sym)
                    if atr_val and atr_val > 0:
                        trail_sl = self._entry_high[sym] - self.trail_atr_k * atr_val
                        if trail_sl < last_f:
                            sl = max(sl, trail_sl)
                            self.stops[sym] = (sl, tp)
            except Exception:
                pass

            timed_out = False
            ent_ts = self.entry_ts.get(sym)
            if ent_ts is not None and (now_ts - ent_ts) >= self.time_stop_s:
                timed_out = True

            # Partial take-profits
            try:
                stage = self._partial_stage.get(sym, 0)
                ep = self._entry_price.get(sym, last_f)
                r_unit = max(1e-9, ep - sl)
                if (
                    self.partial_r1 > 0
                    and stage == 0
                    and last_f >= ep + self.partial_r1 * r_unit
                ):
                    q1 = max(pos.base * 0.5, 0.0)
                    if q1 > 0:
                        await self.execman.submit(
                            sym,
                            "sell",
                            "market",
                            q1,
                            None,
                            l1,
                            "partial_tp",
                            {"target": "R1"},
                            {"whitelist": True, "spot_only": True, "long_only": True},
                        )
                        self._partial_stage[sym] = 1
                elif (
                    self.partial_r2 > 0
                    and stage == 1
                    and last_f >= ep + self.partial_r2 * r_unit
                ):
                    q2 = max(pos.base * 0.5, 0.0)
                    if q2 > 0:
                        await self.execman.submit(
                            sym,
                            "sell",
                            "market",
                            q2,
                            None,
                            l1,
                            "partial_tp",
                            {"target": "R2"},
                            {"whitelist": True, "spot_only": True, "long_only": True},
                        )
                        self._partial_stage[sym] = 2
            except Exception:
                pass

            if last_f <= sl or last_f >= tp or timed_out:
                qty = pos.base
                reason = (
                    "sl" if last_f <= sl else ("tp" if last_f >= tp else "time_stop")
                )
                features = {"exit_reason": reason}
                checks = {"whitelist": True, "spot_only": True, "long_only": True}
                # Micro-sliced exits
                slice_qty = max(qty / max(1, self.micro_slices), 0.0)
                remaining = qty
                for i in range(self.micro_slices):
                    q = slice_qty if i < self.micro_slices - 1 else remaining
                    if q <= 0:
                        break
                    await self.execman.submit(
                        sym,
                        "sell",
                        "market",
                        q,
                        None,
                        l1,
                        "stop_exit",
                        features,
                        checks,
                    )
                    remaining -= q
                # Log MFE/MAE
                try:
                    ep = self._entry_price.get(sym, last_f)
                    r_pct = (ep - sl) / ep if ep > 0 else 0.0
                    mfe = self._mfe_pct.get(sym, 0.0)
                    mae = self._mae_pct.get(sym, 0.0)
                    self.execman.ctx.ledger.append(
                        {
                            "ts": now_ts,
                            "event": "trade_mfe_mae",
                            "symbol": sym,
                            "entry": ep,
                            "exit": last_f,
                            "mfe_pct": mfe,
                            "mae_pct": mae,
                            "mfe_R": (mfe / (r_pct if r_pct != 0 else 1.0))
                            if r_pct > 0
                            else None,
                            "mae_R": (mae / (r_pct if r_pct != 0 else 1.0))
                            if r_pct > 0
                            else None,
                        }
                    )
                    METRICS.record_mfe_mae(mfe, mae)
                except Exception:
                    pass
                # Clear state
                self.stops.pop(sym, None)
                self.entry_ts.pop(sym, None)
                self._entry_high.pop(sym, None)
                self._entry_price.pop(sym, None)
                self._mfe_pct.pop(sym, None)
                self._mae_pct.pop(sym, None)
                self._pyramids_done.pop(sym, None)
                self._partial_stage.pop(sym, None)
                return

        # Max open positions
        open_positions = sum(1 for p in self.portfolio.positions.values() if p.base > 0)
        if not self.risk.can_open_new_position(open_positions):
            return

        # ATR filters & no-trade window
        atr_val = self._compute_atr(sym)
        atr_pct = (atr_val / last_f) if (atr_val and last_f > 0) else 0.0
        out = False
        if self.min_atr_pct and atr_pct < self.min_atr_pct:
            out = True
        if self.max_atr_pct and atr_pct > self.max_atr_pct:
            out = True
        if out:
            self._atr_out_count[sym] = self._atr_out_count.get(sym, 0) + 1
            if (
                self.atr_block_consec > 0
                and self._atr_out_count[sym] >= self.atr_block_consec
            ):
                self._atr_block_until[sym] = now_ts + float(
                    max(0, self.atr_block_cooldown_s)
                )
                self._atr_out_count[sym] = 0
            return
        else:
            # Normalize: reduce or reset counters; early unblock if in band long enough
            self._atr_out_count[sym] = 0
            if (
                self._atr_block_until.get(sym, 0.0) > now_ts
                and self.atr_block_consec > 0
            ):
                # Require half the threshold in-band to unblock
                unblock_need = max(1, self.atr_block_consec // 2)
                cnt = abs(self._atr_out_count.get(sym, 0)) + 1
                self._atr_out_count[sym] = -cnt
                if cnt >= unblock_need:
                    self._atr_block_until[sym] = now_ts
        if self._atr_block_until.get(sym, 0.0) > now_ts:
            return

        # EMA confirmation
        if self.ema_fast_n > 1 and self.ema_slow_n > 1:
            ef = self._ema_fast.get(sym)
            es = self._ema_slow.get(sym)
            if ef is None or es is None or not (ef > es):
                return

        # Strategy priority
        strats = [self.momo[sym], self.meanrev[sym]]
        if sym in self.ml:
            strats.insert(0, self.ml[sym])
        for strat in strats:
            sig = strat.on_tick(l1)
            if not sig:
                continue
            if sig.get("action") == "buy":
                if getattr(strat, "id", "") == "ml":
                    try:
                        ML_SIGNALS.labels(sym).inc()
                        proba = (
                            float(sig.get("features", {}).get("proba", 0.0))
                            if isinstance(sig.get("features"), dict)
                            else None
                        )
                        self.execman.ctx.ledger.append(
                            {
                                "ts": now_ts,
                                "event": "ml_signal",
                                "symbol": sym,
                                "proba": proba,
                                "mode": "paper"
                                if not self.execman.ctx.live_enabled
                                else "live",
                            }
                        )
                    except Exception:
                        pass
                qty = self.risk.sizer(self.portfolio.equity({sym: last_f}), last_f)
                if qty <= 0:
                    continue
                sl, tp = self.risk.sl_tp_levels(
                    last_f, atr=atr_val if atr_val else None
                )
                features = (
                    sig.get("features", {})
                    if isinstance(sig.get("features"), dict)
                    else {}
                )
                features.update({"sl": sl, "tp": tp})
                checks = {"whitelist": True, "spot_only": True, "long_only": True}
                slice_qty = max(qty / max(1, self.micro_slices), 0.0)
                remaining = qty
                for i in range(self.micro_slices):
                    q = slice_qty if i < self.micro_slices - 1 else remaining
                    if q <= 0:
                        break
                    await self.execman.submit(
                        sym,
                        "buy",
                        "market",
                        q,
                        None,
                        l1,
                        getattr(strat, "id", ""),
                        features,
                        checks,
                    )
                    remaining -= q
                self.stops[sym] = (sl, tp)
                self.entry_ts.setdefault(sym, now_ts)
                self._entry_high[sym] = last_f
                self._entry_price.setdefault(sym, last_f)
                self._mfe_pct[sym] = 0.0
                self._mae_pct[sym] = 0.0
                # Pyramids counter reset
                self._pyramids_done[sym] = 0
                break

        # Pyramiding logic
        pos = self.portfolio.get_position(sym)
        if self.pyramid_max > 0 and pos.base > 0 and sym in self._entry_price:
            done = self._pyramids_done.get(sym, 0)
            if done < self.pyramid_max and self.pyramid_step_pct > 0:
                trigger_px = self._entry_price[sym] * (
                    1.0 + self.pyramid_step_pct * (done + 1)
                )
                if last_f >= trigger_px:
                    add_qty = max(
                        self.risk.sizer(self.portfolio.equity({sym: last_f}), last_f)
                        / max(2, self.micro_slices),
                        0.0,
                    )
                    if add_qty > 0:
                        await self.execman.submit(
                            sym,
                            "buy",
                            "market",
                            add_qty,
                            None,
                            l1,
                            "pyramid",
                            {"trigger_px": trigger_px},
                            {"whitelist": True, "spot_only": True, "long_only": True},
                        )
                        self._pyramids_done[sym] = done + 1
        # Update equity metric (approximate with current symbol price)
        try:
            METRICS.update_equity(self.portfolio.equity({sym: last_f}))
        except Exception:
            pass

    def _compute_atr(self, sym: str) -> Optional[float]:
        if self._atr_window <= 0:
            return None
        dq = self._ohlc.get(sym)
        if not dq or len(dq) < self._atr_window + 1:
            return None
        it = iter(dq)
        prev_hi, prev_lo, prev_close = next(it)
        trs: List[float] = []
        for hi, lo, cl in it:
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
            prev_close = cl
        if len(trs) < self._atr_window:
            return None
        return sum(trs[-self._atr_window :]) / float(self._atr_window)

    # Runtime application of parameter overrides
    def apply_params(self, params: Dict[str, Dict[str, float]] | None) -> None:
        if not params:
            return
        # Strategies
        for s, m in self.momo.items():
            for k, v in (params.get("momentum", {}) or {}).items():
                if hasattr(m, k):
                    try:
                        setattr(m, k, type(getattr(m, k))(v))  # type: ignore
                    except Exception:
                        setattr(m, k, v)
        for s, r in self.meanrev.items():
            for k, v in (params.get("meanrev", {}) or {}).items():
                if hasattr(r, k):
                    try:
                        setattr(r, k, type(getattr(r, k))(v))  # type: ignore
                    except Exception:
                        setattr(r, k, v)
        # ML params: dynamically attach/detach
        ml_params = params.get("ml", {}) if isinstance(params, dict) else {}
        try:
            enabled = bool(ml_params.get("enabled", False))
            if enabled:
                from .strategies.ml import MLStrategy  # local import

                model_path = str(
                    ml_params.get("model_path", "artifacts/models/ml_pipeline.joblib")
                )
                prob_cut = float(ml_params.get("prob_cut", 0.6))
                for s in self.symbols:
                    if s not in self.ml:
                        self.ml[s] = MLStrategy(
                            symbol=s, model_path=model_path, prob_cut=prob_cut
                        )
                    else:
                        try:
                            self.ml[s].prob_cut = prob_cut  # type: ignore[attr-defined]
                        except Exception:
                            pass
            else:
                # disable ML by clearing dict (keeps object references out of routing)
                self.ml.clear()
        except Exception:
            pass
        # Execution
        ex_params = params.get("execution", {}) if isinstance(params, dict) else {}
        if isinstance(ex_params, dict):
            self.micro_slices = int(ex_params.get("micro_slices", self.micro_slices))
            self.time_stop_s = int(ex_params.get("time_stop_s", self.time_stop_s))
            self.trail_atr_k = float(ex_params.get("trail_atr_k", self.trail_atr_k))
            self.pyramid_max = int(ex_params.get("pyramid_max", self.pyramid_max))
            self.pyramid_step_pct = float(
                ex_params.get("pyramid_step_pct", self.pyramid_step_pct)
            )
            self.partial_r1 = float(ex_params.get("partial_r1", self.partial_r1))
            self.partial_r2 = float(ex_params.get("partial_r2", self.partial_r2))
        # Filters
        flt = params.get("filters", {}) if isinstance(params, dict) else {}
        if isinstance(flt, dict):
            self.ema_fast_n = int(flt.get("ema_fast", self.ema_fast_n))
            self.ema_slow_n = int(flt.get("ema_slow", self.ema_slow_n))
            self.min_atr_pct = float(flt.get("min_atr_pct", self.min_atr_pct))
            self.max_atr_pct = float(flt.get("max_atr_pct", self.max_atr_pct))
            self.atr_block_consec = int(
                flt.get("atr_block_consec", self.atr_block_consec)
            )
            self.atr_block_cooldown_s = int(
                flt.get("atr_block_cooldown_s", self.atr_block_cooldown_s)
            )


__all__ = ["StrategyRouter"]
