from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)
    cl = df["close"].astype(float)
    prev_close = cl.shift(1)
    tr = np.maximum(
        hi - lo, np.maximum((hi - prev_close).abs(), (lo - prev_close).abs())
    )
    return tr.rolling(window, min_periods=window).mean()


def rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    cl = df["close"].astype(float)
    delta = cl.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(window, min_periods=window).mean()
    roll_down = down.rolling(window, min_periods=window).mean()
    rs = roll_up / (roll_down.replace(0, np.nan))
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


def rolling_z(series: pd.Series, window: int) -> pd.Series:
    m = series.rolling(window, min_periods=window).mean()
    s = series.rolling(window, min_periods=window).std(ddof=0)
    z = (series - m) / (s.replace(0, np.nan))
    return z.fillna(0.0)


def compute_features(
    df: pd.DataFrame, lookbacks: Iterable[int] = (5, 10, 20)
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index.copy())
    cl = df["close"].astype(float)
    out["ret_1"] = cl.pct_change().fillna(0.0)
    for w in lookbacks:
        out[f"ret_{w}"] = cl.pct_change(w).fillna(0.0)
        out[f"z_ret_{w}"] = rolling_z(out["ret_1"], w)
    out["atr14"] = atr(df, 14).fillna(0.0)
    out["rsi14"] = rsi(df, 14)
    out["vol"] = (
        df.get("volume", pd.Series(index=df.index, dtype=float))
        .astype(float)
        .fillna(0.0)
    )
    out = out.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    return out
