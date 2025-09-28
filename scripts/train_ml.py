from __future__ import annotations

import argparse
import json
from pathlib import Path


import pandas as pd

from src.ml.dataset import load_ohlcv_many
from src.ml.features import compute_features
from src.ml.labels import triple_barrier_labels
from src.ml.model import train_pipeline


def build_dataset(
    dfs: dict[str, pd.DataFrame], atr_mult: float = 1.0, horizon: int = 10
) -> tuple[pd.DataFrame, pd.Series]:
    frames = []
    ys = []
    for sym, df in dfs.items():
        if df.empty:
            continue
        df = df.sort_values("ts").reset_index(drop=True)
        df.set_index(pd.to_datetime(df["ts"], unit="ms", utc=True), inplace=True)
        feats = compute_features(df)
        lab = triple_barrier_labels(df, horizon=horizon, atr_mult=atr_mult)
        feats = feats.iloc[:-horizon]
        lab = lab.iloc[:-horizon]
        # MultiIndex: (symbol, ts) to avoid collisions across symbols
        feats.index = pd.MultiIndex.from_arrays(
            [pd.Index([sym] * len(feats), name="symbol"), feats.index],
            names=["symbol", "ts"],
        )  # type: ignore
        lab.index = pd.MultiIndex.from_arrays(
            [pd.Index([sym] * len(lab), name="symbol"), lab.index],
            names=["symbol", "ts"],
        )  # type: ignore
        frames.append(feats)
        ys.append(lab)
    X = pd.concat(frames).dropna()
    y_all = pd.concat(ys)
    y = y_all.loc[X.index]
    return X, y


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", type=str, required=True)
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--timeframe", type=str, default="1m")
    p.add_argument("--exchange", type=str, default="binance")
    p.add_argument("--atr-mult", type=float, default=1.0)
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--prob-cut", type=float, default=0.6)
    p.add_argument("--data-dir", type=str, default="data")
    p.add_argument("--out-dir", type=str, default="artifacts/models")
    ns = p.parse_args()

    import pandas as pd  # lazy import

    start_ms = int(pd.Timestamp(ns.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(ns.end, tz="UTC").timestamp() * 1000)
    symbols: list[str] = [s.strip() for s in ns.symbols.split(",") if s.strip()]

    dfs = load_ohlcv_many(
        symbols, ns.timeframe, start_ms, end_ms, Path(ns.data_dir), ns.exchange
    )
    X, y = build_dataset(dfs, atr_mult=ns.atr_mult, horizon=ns.horizon)
    pipe, score = train_pipeline(X, y)

    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import joblib

    model_path = out_dir / "ml_pipeline.joblib"
    joblib.dump(pipe, model_path)
    cfg = {
        "prob_cut": ns.prob_cut,
        "timeframe": ns.timeframe,
        "atr_mult": ns.atr_mult,
        "horizon": ns.horizon,
        "exchange": ns.exchange,
        "score_in_sample": score,
        "model_path": str(model_path),
    }
    (out_dir / "ml_config.json").write_text(json.dumps(cfg, indent=2))
    print(f"Saved model to {model_path} (score {score:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
