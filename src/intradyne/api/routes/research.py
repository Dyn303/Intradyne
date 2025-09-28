from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from prometheus_client import Gauge

from backtester.engine import run_backtest
# metrics available for future use


router = APIRouter()


_TUNING_LAST_SCORE = Gauge(
    "tuning_last_score",
    "Last saved tuned score",
    labelnames=("metric",),
)
_TUNING_LAST_APPLIED_SCORE = Gauge(
    "tuning_last_applied_score",
    "Last applied tuned score",
    labelnames=("metric",),
)


@router.get("/research/backtest_ma")
async def research_backtest_ma(
    days: int = Query(30, ge=1, le=365),
    symbols: str = Query("BTC/USDT,ETH/USDT"),
    ma: int = Query(20, ge=2, le=200),
) -> Dict[str, Any]:
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    rep = run_backtest(days=days, symbols=syms, ma_window=ma, report=True)
    if not isinstance(rep, dict):
        raise HTTPException(status_code=500, detail="unexpected_report_shape")
    return rep


@router.get("/research/optimize_ma")
async def research_optimize_ma(
    days: int = Query(30, ge=1, le=90),
    symbols: str = Query("BTC/USDT,ETH/USDT"),
    ma_min: int = Query(10, ge=2, le=200),
    ma_max: int = Query(60, ge=3, le=300),
    step: int = Query(5, ge=1, le=50),
    metric: str = Query("sharpe", pattern="^(sharpe|winrate|avg_return)$"),
) -> Dict[str, Any]:
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    best = None
    best_val = float("-inf")
    results: List[Dict[str, Any]] = []
    for m in range(ma_min, ma_max + 1, step):
        rep_obj = run_backtest(days=days, symbols=syms, ma_window=m, report=True)
        assert isinstance(rep_obj, dict)
        rep: Dict[str, Any] = rep_obj
        val = 0.0
        if metric == "sharpe":
            # approximate using avg_return/std via sharpe on per-trade returns across symbols
            # The engine doesn't return raw per-trade returns; proxy with avg_return here
            v = rep.get("avg_return", 0.0)
            val = float(v) if isinstance(v, (int, float)) else 0.0
        elif metric == "winrate":
            v = rep.get("winrate", 0.0)
            val = float(v) if isinstance(v, (int, float)) else 0.0
        elif metric == "avg_return":
            v = rep.get("avg_return", 0.0)
            val = float(v) if isinstance(v, (int, float)) else 0.0
        results.append({"ma": m, metric: val, "report": rep})
        if val > best_val:
            best_val = val
            best = {"ma": m, metric: val}
    return {"best": best, "results": results}


@router.get("/research/optimize_ma_trend")
async def research_optimize_ma_trend(
    days: int = Query(30, ge=1, le=90),
    symbols: str = Query("BTC/USDT,ETH/USDT"),
    ma_min: int = Query(10, ge=2, le=200),
    ma_max: int = Query(60, ge=3, le=300),
    trend_min: int = Query(20, ge=2, le=300),
    trend_max: int = Query(100, ge=3, le=500),
    step: int = Query(5, ge=1, le=50),
    risk_pct: float = Query(0.0, ge=0.0, le=0.05),
    sl_k: float = Query(0.0, ge=0.0, le=5.0),
    tp_k: float = Query(0.0, ge=0.0, le=5.0),
    metric: str = Query("winrate", pattern="^(winrate|avg_return)$"),
) -> Dict[str, Any]:
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    best = None
    best_val = float("-inf")
    results: List[Dict[str, Any]] = []
    for m in range(ma_min, ma_max + 1, step):
        for tr in range(trend_min, trend_max + 1, step):
            rep_obj = run_backtest(
                days=days,
                symbols=syms,
                ma_window=m,
                trend_ema=tr,
                risk_per_trade=risk_pct,
                sl_atr_k=sl_k,
                tp_atr_k=tp_k,
                report=True,
            )
            assert isinstance(rep_obj, dict)
            rep: Dict[str, Any] = rep_obj
            v = rep.get(metric, 0.0)
            val = float(v) if isinstance(v, (int, float)) else 0.0
            rec = {"ma": m, "trend_ema": tr, metric: val, "report": rep}
            results.append(rec)
            if val > best_val:
                best_val = val
                best = {"ma": m, "trend_ema": tr, metric: val}
    return {"best": best, "results": results}


@router.get("/research/optimize_params")
async def research_optimize_params(
    days: int = Query(30, ge=1, le=60),
    symbols: str = Query("BTC/USDT,ETH/USDT"),
    ma_min: int = Query(10, ge=2, le=200),
    ma_max: int = Query(40, ge=3, le=300),
    trend_min: int = Query(20, ge=2, le=300),
    trend_max: int = Query(100, ge=3, le=500),
    step: int = Query(10, ge=1, le=50),
    sl_min: float = Query(0.5, ge=0.0, le=5.0),
    sl_max: float = Query(2.0, ge=0.0, le=5.0),
    tp_min: float = Query(1.0, ge=0.0, le=5.0),
    tp_max: float = Query(3.0, ge=0.0, le=5.0),
    risk_min: float = Query(0.002, ge=0.0, le=0.05),
    risk_max: float = Query(0.02, ge=0.0, le=0.10),
    risk_step: float = Query(0.004, ge=0.0005, le=0.05),
    metric: str = Query("winrate", pattern="^(winrate|avg_return)$"),
    save: int = Query(0, ge=0, le=1),
) -> Dict[str, Any]:
    """Grid search MA, trend EMA, SL/TP ATR multipliers, risk per trade.

    Keeps the grid coarse by default to stay responsive. Use narrower ranges to refine.
    If `save=1`, persists best params to `artifacts/tuned_profile.json`.
    """
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    best = None
    best_val = float("-inf")
    results: List[Dict[str, Any]] = []

    # Build coarse grids
    def _frange(a: float, b: float, st: float) -> List[float]:
        xs: List[float] = []
        x = a
        while x <= b + 1e-12:
            xs.append(round(x, 6))
            x += st
        return xs

    sls = _frange(sl_min, sl_max, max(0.1, (sl_max - sl_min) / 2.0))
    tps = _frange(tp_min, tp_max, max(0.2, (tp_max - tp_min) / 2.0))
    risks = _frange(risk_min, risk_max, risk_step)

    for m in range(ma_min, ma_max + 1, step):
        for tr in range(trend_min, trend_max + 1, step):
            for sk in sls:
                for tk in tps:
                    for rp in risks:
                        rep_obj = run_backtest(
                            days=days,
                            symbols=syms,
                            ma_window=m,
                            trend_ema=tr,
                            risk_per_trade=rp,
                            sl_atr_k=sk,
                            tp_atr_k=tk,
                            report=True,
                        )
                        assert isinstance(rep_obj, dict)
                        rep: Dict[str, Any] = rep_obj
                        v = rep.get(metric, 0.0)
                        val = float(v) if isinstance(v, (int, float)) else 0.0
                        rec = {
                            "ma": m,
                            "trend_ema": tr,
                            "sl_k": sk,
                            "tp_k": tk,
                            "risk_pct": rp,
                            metric: val,
                            "report": rep,
                        }
                        results.append(rec)
                        if val > best_val:
                            best_val = val
                            best = {
                                "ma": m,
                                "trend_ema": tr,
                                "sl_k": sk,
                                "tp_k": tk,
                                "risk_pct": rp,
                                metric: val,
                            }

    # Optional persistence
    saved: Dict[str, Any] | None = None
    if save and best is not None:
        try:
            import os
            from datetime import datetime
            import orjson

            os.makedirs("artifacts", exist_ok=True)
            payload = {
                "profile": "auto",
                "created": datetime.utcnow().isoformat() + "Z",
                "params": {
                    "ma_n": best["ma"],
                    "trend_ema": best["trend_ema"],
                    "atr_sl_k": best["sl_k"],
                    "atr_tp_k": best["tp_k"],
                    "risk_per_trade_pct": best["risk_pct"],
                },
                "metric": metric,
                "score": best_val,
            }
            path = os.path.join("artifacts", "tuned_profile.json")
            # Guard: only save if improved over existing score
            prev_score = None
            try:
                with open(path, "rb") as f:
                    prev = orjson.loads(f.read())
                    ps = prev.get("score")
                    if isinstance(ps, (int, float)):
                        prev_score = float(ps)
            except FileNotFoundError:
                prev_score = None
            except Exception:
                prev_score = None

            if prev_score is None or best_val > prev_score:
                with open(path, "wb") as wf:
                    wf.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
                saved = {"path": path, "improved": True, "prev_score": prev_score}
                try:
                    _TUNING_LAST_SCORE.labels(metric).set(float(best_val))
                except Exception:
                    pass
            else:
                saved = {
                    "skipped": True,
                    "reason": "not_improved",
                    "prev_score": prev_score,
                    "new_score": best_val,
                }
        except Exception as e:  # noqa: BLE001
            saved = {"error": str(e)}

    return {"best": best, "saved": saved, "count": len(results)}


@router.get("/research/tuning/baseline")
async def tuning_baseline() -> Dict[str, Any]:
    """Return current tuned score/metric and last applied score/metric.

    Reads `artifacts/tuned_profile.json` and `artifacts/production_params.json`.
    Also updates Prometheus gauges when present.
    """
    import os
    import orjson

    tuned_path = os.path.join("artifacts", "tuned_profile.json")
    applied_path = os.path.join("artifacts", "production_params.json")
    tuned: Dict[str, Any] | None = None
    applied: Dict[str, Any] | None = None

    try:
        with open(tuned_path, "rb") as f:
            tuned = orjson.loads(f.read())
            m = tuned.get("metric")
            s = tuned.get("score")
            if isinstance(m, str) and isinstance(s, (int, float)):
                try:
                    _TUNING_LAST_SCORE.labels(m).set(float(s))
                except Exception:
                    pass
    except FileNotFoundError:
        tuned = None
    except Exception:
        tuned = None

    try:
        with open(applied_path, "rb") as f:
            ap_raw = orjson.loads(f.read())
            meta = ap_raw.get("tuning_meta", {}) if isinstance(ap_raw, dict) else {}
            applied = meta if isinstance(meta, dict) else None
            m2 = (applied or {}).get("metric")
            s2 = (applied or {}).get("score")
            if isinstance(m2, str) and isinstance(s2, (int, float)):
                try:
                    _TUNING_LAST_APPLIED_SCORE.labels(m2).set(float(s2))
                except Exception:
                    pass
    except FileNotFoundError:
        applied = None
    except Exception:
        applied = None

    out: Dict[str, Any] = {"current_tuned": tuned or {}, "last_applied": applied or {}}
    return out


@router.get("/research/profile/best")
async def research_profile_best() -> Dict[str, Any]:
    import os
    import orjson

    path = os.path.join("artifacts", "tuned_profile.json")
    try:
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
        return data
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/research/profile/apply")
async def research_profile_apply() -> Dict[str, Any]:
    """Marks the tuned profile as active and records to the ledger.

    In this lightweight build, applying just writes an 'applied' marker file and
    appends a ledger event. Strategy components can read `artifacts/tuned_profile.json`.
    """
    import os
    from datetime import datetime
    import orjson
    from intradyne.api.deps import get_ledger

    led = get_ledger()
    path = os.path.join("artifacts", "tuned_profile.json")
    try:
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no_tuned_profile")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    # record application
    led.append(
        "profile_apply",
        {
            "profile": data.get("profile", "auto"),
            "ts": datetime.utcnow().isoformat() + "Z",
        },
    )
    # write simple marker
    try:
        with open(
            os.path.join("artifacts", "active_profile.txt"), "w", encoding="utf-8"
        ) as f:
            f.write(data.get("profile", "auto"))
    except Exception:
        pass
    return {
        "applied": True,
        "profile": data.get("profile", "auto"),
        "params": data.get("params", {}),
    }
