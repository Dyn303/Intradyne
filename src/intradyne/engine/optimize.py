from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import optuna
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler

from .config import load_settings
from .backtest import run as run_backtest


def _score(
    metrics: Dict[str, Any],
    objective: str,
    lam_dd: float,
    lam_var: float,
    days: float = 1.0,
    target_daily: float = 0.0,
) -> float:
    sharpe = float(metrics.get("sharpe", 0.0))
    net = float(metrics.get("net_pnl", 0.0))
    max_dd = float(metrics.get("max_dd", 0.0))
    if objective == "sharpe":
        return sharpe - lam_dd * max_dd
    elif objective == "pnl":
        return net - lam_dd * max_dd
    elif objective in ("daily", "daily_pct"):
        # Compounded daily growth from start and final equity
        final_eq = float(metrics.get("final_equity", 0.0))
        start_eq = final_eq - net if final_eq or net else 0.0
        daily = 0.0
        if start_eq > 0 and days > 0:
            daily = (final_eq / start_eq) ** (1.0 / days) - 1.0
        # Optionally bias towards/exceeding target
        bonus = 0.0
        if target_daily > 0:
            bonus = max(0.0, daily - target_daily)
        return daily + bonus - lam_dd * max_dd
    else:
        return net + sharpe - lam_dd * max_dd


def suggest_params(trial: optuna.Trial) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "momentum": {
            "breakout_window": trial.suggest_int("m_breakout_window", 5, 240),
            "min_range_bps": trial.suggest_int("m_min_range_bps", 1, 30),
        },
        "meanrev": {
            "window": trial.suggest_int("r_window", 10, 240),
            "k": trial.suggest_float("r_band_width", 1.0, 5.0),
        },
        "risk": {
            "max_pos_pct": trial.suggest_float("risk_max_pos_pct", 0.005, 0.05),
            "dd_soft": trial.suggest_float("risk_dd_soft", 0.02, 0.08),
            "dd_hard": trial.suggest_float("risk_dd_hard", 0.03, 0.10),
            "per_trade_sl_pct": trial.suggest_float("risk_sl_pct", 0.0005, 0.02),
            "tp_pct": trial.suggest_float("risk_tp_pct", 0.001, 0.03),
            # ATR-based exits
            "use_atr": trial.suggest_categorical("risk_use_atr", [True, False]),
            "atr_window": trial.suggest_int("risk_atr_window", 5, 50),
            "atr_k_sl": trial.suggest_float("risk_atr_k_sl", 0.5, 3.0),
            "atr_k_tp": trial.suggest_float("risk_atr_k_tp", 0.5, 5.0),
        },
        "execution": {
            "micro_slices": trial.suggest_categorical(
                "exec_micro_slices", [3, 5, 7, 9]
            ),
            "time_stop_s": trial.suggest_int("exec_time_stop_s", 10, 360),
        },
        "ml": {
            "enabled": True,
            "model_path": "artifacts/models/ml_pipeline.joblib",
            "prob_cut": trial.suggest_float("ml_prob_cut", 0.5, 0.8),
        },
    }
    return params


def optimize(
    symbols: List[str],
    start_ms: int,
    end_ms: int,
    timeframe: str,
    strategy: str,
    n_trials: int,
    n_jobs: int,
    objective: str,
    lam_dd: float,
    min_trades_per_day: int = 0,
    target_daily_pct: float = 0.0,
    hard_target_daily_pct: float = 0.0,
    study_name: str = "intradyne_hyperopt",
) -> Path:
    settings = load_settings()
    artifacts = Path(settings.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    storage = settings.optuna_db_url
    study = optuna.create_study(
        direction="maximize",
        storage=storage,
        study_name=study_name,
        load_if_exists=True,
        sampler=TPESampler(seed=42),
        pruner=MedianPruner(),
    )

    def obj(trial: optuna.Trial) -> float:
        params = suggest_params(trial)
        try:
            res = run_backtest(
                symbols,
                start_ms,
                end_ms,
                timeframe,
                strategy,
                params,
                maker_bps=2,
                taker_bps=5,
                slippage_bps=2,
                seed=42,
                fast_mode=True,
                early_target_trades_per_day=min_trades_per_day
                if min_trades_per_day > 0
                else None,
            )
        except Exception as e:
            # Non-compliant or failure => prune
            raise optuna.TrialPruned(f"invalid trial: {e}")
        # Enforce minimum trades per day if requested
        if min_trades_per_day > 0:
            days = max(1.0, (end_ms - start_ms) / (1000.0 * 86400.0))
            trades = float(res.metrics.get("trades", 0))
            if trades / days < float(min_trades_per_day):
                raise optuna.TrialPruned(
                    f"too few trades: {trades / days:.2f} < {min_trades_per_day}"
                )
        days = max(1.0, (end_ms - start_ms) / (1000.0 * 86400.0))
        # Early prune if hard daily target specified and unmet
        if objective in ("daily", "daily_pct") and hard_target_daily_pct > 0:
            final_eq = float(res.metrics.get("final_equity", 0.0))
            start_eq = (
                final_eq - float(res.metrics.get("net_pnl", 0.0)) if final_eq else 0.0
            )
            if start_eq > 0:
                daily = (final_eq / start_eq) ** (1.0 / days) - 1.0
                if daily < hard_target_daily_pct:
                    raise optuna.TrialPruned(
                        f"daily {daily:.4f} < hard target {hard_target_daily_pct:.4f}"
                    )
        score = _score(
            res.metrics,
            objective,
            lam_dd,
            0.0,
            days=days,
            target_daily=target_daily_pct,
        )
        trial.set_user_attr("metrics", res.metrics)
        return score

    study.optimize(obj, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=False)
    # Handle case where all trials were pruned
    try:
        best = study.best_trial
        best_params = {
            **best.params,
            "metrics": best.user_attrs.get("metrics", {}),
            "strategy": strategy,
        }
    except Exception:
        best_params = {
            "note": "no completed trials",
            "strategy": strategy,
            "study": study_name,
        }
    out = artifacts / "best_params.json"
    out.write_text(json.dumps(best_params, indent=2))
    # Also write strategy-specific file
    out_strat = artifacts / f"best_params_{strategy}.json"
    out_strat.write_text(json.dumps(best_params, indent=2))

    # Optional plots (requires matplotlib)
    try:
        from optuna.visualization.matplotlib import (
            plot_optimization_history,
            plot_param_importances,
        )
        import matplotlib.pyplot as plt

        fig1 = plot_optimization_history(study)
        fig1.figure.savefig(artifacts / "opt_history.png", dpi=150)
        plt.close(fig1.figure)
        fig2 = plot_param_importances(study)
        fig2.figure.savefig(artifacts / "opt_importance.png", dpi=150)
        plt.close(fig2.figure)
    except Exception:
        pass
    return out_strat


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True)
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument(
        "--strategy", type=str, choices=["momentum", "meanrev"], default="momentum"
    )
    p.add_argument("--trials", type=int, default=50)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument(
        "--objective",
        type=str,
        choices=["sharpe", "pnl", "combo", "daily"],
        default="sharpe",
        help="Optimization objective",
    )
    p.add_argument("--lambda-dd", dest="lambda_dd", type=float, default=0.5)
    p.add_argument(
        "--min-trades-per-day", dest="min_trades_per_day", type=int, default=0
    )
    p.add_argument(
        "--target-daily-pct",
        dest="target_daily_pct",
        type=float,
        default=0.0,
        help="Target compounded daily profit (e.g., 0.015 for 1.5%)",
    )
    p.add_argument(
        "--hard-target-daily-pct",
        dest="hard_target_daily_pct",
        type=float,
        default=0.0,
        help="Hard prune trials below this daily target",
    )
    p.add_argument(
        "--study-name", dest="study_name", type=str, default="intradyne_hyperopt"
    )
    return p.parse_args()


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args()
    import pandas as pd

    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start_ms = int(pd.Timestamp(ns.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(ns.end, tz="UTC").timestamp() * 1000)
    optimize(
        symbols,
        start_ms,
        end_ms,
        ns.timeframe,
        ns.strategy,
        ns.trials,
        ns.jobs,
        ns.objective,
        ns.lambda_dd,
        ns.min_trades_per_day,
        ns.target_daily_pct,
        ns.hard_target_daily_pct,
        ns.study_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
