from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier_labels(
    df: pd.DataFrame, horizon: int = 10, atr_mult: float = 1.0
) -> pd.Series:
    """Simple triple-barrier labeling using ATR-based barriers.

    Label 1 if upper barrier hit within horizon, 0 otherwise.
    """
    cl = df["close"].astype(float).to_numpy()
    hi = df["high"].astype(float).to_numpy()
    lo = df["low"].astype(float).to_numpy()
    # ATR proxy: rolling true range mean
    prev_close = np.roll(cl, 1)
    prev_close[0] = cl[0]
    tr = np.maximum(
        hi - lo, np.maximum(np.abs(hi - prev_close), np.abs(lo - prev_close))
    )
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    y = np.zeros(len(cl), dtype=int)
    n = len(cl)
    for i in range(n - horizon):
        a = atr[i] if not np.isnan(atr[i]) else 0.0
        up = cl[i] + atr_mult * a
        dn = cl[i] - atr_mult * a
        upper_hit = False
        for j in range(1, horizon + 1):
            if hi[i + j] >= up:
                upper_hit = True
                break
            if lo[i + j] <= dn:
                upper_hit = False
                break
        y[i] = 1 if upper_hit else 0
    return pd.Series(y, index=df.index, dtype=int)
