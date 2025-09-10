from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import optuna
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler

from .config import load_settings
from .backtest import run as run_backtest


def _score(metrics: Dict[str, Any], objective: str, lam_dd: float, lam_var: float) -> float:
    sharpe = float(metrics.get("sharpe", 0.0))
    net = float(metrics.get("net_pnl", 0.0))
    max_dd = float(metrics.get("max_dd", 0.0))
    if objective == "sharpe":
        return sharpe - lam_dd * max_dd
    elif objective == "pnl":
        return net - lam_dd * max_dd
    else:
        return net + sharpe - lam_dd * max_dd


def suggest_params(trial: optuna.Trial) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "momentum": {
            "breakout_window": trial.suggest_int("m_breakout_window", 20, 180),
            "min_range_bps": trial.suggest_int("m_min_range_bps", 3, 30),
        },
        "meanrev": {
            "window": trial.suggest_int("r_window", 20, 180),
            "k": trial.suggest_float("r_band_width", 1.5, 4.0),
        },
        "risk": {
            "max_pos_pct": trial.suggest_float("risk_max_pos_pct", 0.003, 0.03),
            "dd_soft": trial.suggest_float("risk_dd_soft", 0.02, 0.06),
            "dd_hard": trial.suggest_float("risk_dd_hard", 0.03, 0.08),
            "per_trade_sl_pct": trial.suggest_float("risk_sl_pct", 0.001, 0.01),
            "tp_pct": trial.suggest_float("risk_tp_pct", 0.0005, 0.01),
        },
    }
    return params


def optimize(symbols: List[str], start_ms: int, end_ms: int, timeframe: str, strategy: str, n_trials: int, n_jobs: int, objective: str, lam_dd: float) -> Path:
    settings = load_settings()
    artifacts = Path(settings.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    storage = settings.optuna_db_url
    study = optuna.create_study(direction="maximize", storage=storage, study_name="intradyne_hyperopt", load_if_exists=True, sampler=TPESampler(seed=42), pruner=MedianPruner())

    def obj(trial: optuna.Trial) -> float:
        params = suggest_params(trial)
        try:
            res = run_backtest(symbols, start_ms, end_ms, timeframe, strategy, params, maker_bps=2, taker_bps=5, slippage_bps=2, seed=42)
        except Exception as e:
            # Non-compliant or failure => prune
            raise optuna.TrialPruned(f"invalid trial: {e}")
        score = _score(res.metrics, objective, lam_dd, 0.0)
        trial.set_user_attr("metrics", res.metrics)
        return score

    study.optimize(obj, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=False)
    best = study.best_trial
    best_params = {**best.params, "metrics": best.user_attrs.get("metrics", {}), "strategy": strategy}
    out = artifacts / "best_params.json"
    out.write_text(json.dumps(best_params, indent=2))
    # Also write strategy-specific file
    out_strat = artifacts / f"best_params_{strategy}.json"
    out_strat.write_text(json.dumps(best_params, indent=2))

    # Optional plots (requires matplotlib)
    try:
        from optuna.visualization.matplotlib import plot_optimization_history, plot_param_importances
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
    p.add_argument("--strategy", type=str, choices=["momentum", "meanrev"], default="momentum")
    p.add_argument("--trials", type=int, default=50)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--objective", type=str, choices=["sharpe", "pnl", "combo"], default="sharpe")
    p.add_argument("--lambda-dd", dest="lambda_dd", type=float, default=0.5)
    return p.parse_args()


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args()
    import pandas as pd

    symbols = [s.strip() for s in ns.symbols.split(",") if s.strip()]
    start_ms = int(pd.Timestamp(ns.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(ns.end, tz="UTC").timestamp() * 1000)
    optimize(symbols, start_ms, end_ms, ns.timeframe, ns.strategy, ns.trials, ns.jobs, ns.objective, ns.lambda_dd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
