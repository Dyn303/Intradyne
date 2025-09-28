from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Deque, Dict, Optional
import numpy as np


class MLStrategy:
    id = "ml"

    def __init__(self, symbol: str, model_path: str, prob_cut: float = 0.6) -> None:
        self.symbol = symbol
        self.model_path = model_path
        self.prob_cut = prob_cut
        self._pipe = None
        try:
            import joblib  # type: ignore

            self._pipe = joblib.load(Path(model_path))
        except Exception:
            self._pipe = None
        # minimal rolling buffers
        self._cl: Deque[float] = deque(maxlen=50)
        self._hi: Deque[float] = deque(maxlen=50)
        self._lo: Deque[float] = deque(maxlen=50)
        self._vol: Deque[float] = deque(maxlen=50)

    def on_tick(self, l1: Dict[str, object]) -> Optional[Dict[str, object]]:
        if self._pipe is None:
            return None
        last = float(l1.get("last") or l1.get("bid") or l1.get("ask") or 0.0)
        if last <= 0:
            return None
        self._cl.append(last)
        self._hi.append(float(l1.get("high", last)))
        self._lo.append(float(l1.get("low", last)))
        self._vol.append(float(l1.get("volume", 0.0)))
        if len(self._cl) < 21:
            return None
        # Simple feature vector matching training (subset)
        cl = np.array(self._cl, dtype=float)
        hi = np.array(self._hi, dtype=float)
        lo = np.array(self._lo, dtype=float)
        vol = np.array(self._vol, dtype=float)
        ret1 = (cl[-1] - cl[-2]) / (cl[-2] or 1e-9)

        def pct_change(n: int) -> float:
            den = cl[-n - 1] if len(cl) > n else cl[0]
            return (cl[-1] - den) / (den or 1e-9)

        ret5 = pct_change(5)
        ret10 = pct_change(10)
        ret20 = pct_change(20)
        # ATR14 proxy
        prev_close = cl[-2]
        tr_last = max(
            hi[-1] - lo[-1], abs(hi[-1] - prev_close), abs(lo[-1] - prev_close)
        )
        atr14 = (
            float(
                np.mean(
                    [
                        max(
                            hi[i] - lo[i],
                            abs(hi[i] - cl[i - 1]),
                            abs(lo[i] - cl[i - 1]),
                        )
                        for i in range(-14, 0)
                    ]
                )
            )
            if len(cl) >= 15
            else tr_last
        )
        rsi14 = _rsi(cl, 14)
        feats = np.array(
            [[ret1, ret5, ret10, ret20, atr14, rsi14, vol[-1]]], dtype=float
        )
        try:
            proba = float(self._pipe.predict_proba(feats)[0, 1])
        except Exception:
            return None
        if proba >= self.prob_cut:
            return {"action": "buy", "features": {"proba": proba}}
        return None


def _rsi(cl: np.ndarray, window: int) -> float:
    if len(cl) <= window:
        return 50.0
    diff = np.diff(cl[-(window + 1) :])
    up = np.clip(diff, 0, None).mean()
    down = (-np.clip(diff, None, 0)).mean()
    if down == 0:
        return 100.0
    rs = up / down
    return 100.0 - (100.0 / (1.0 + rs))
