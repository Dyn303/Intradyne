from __future__ import annotations

from typing import Tuple
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler(with_mean=True, with_std=True)),
            (
                "clf",
                LogisticRegression(max_iter=1000, n_jobs=None, class_weight="balanced"),
            ),
        ]
    )


def train_pipeline(X: pd.DataFrame, y: pd.Series) -> Tuple[Pipeline, float]:
    pipe = build_pipeline()
    pipe.fit(X.values, y.values)
    # Simple in-sample score
    score = float(pipe.score(X.values, y.values))
    return pipe, score
