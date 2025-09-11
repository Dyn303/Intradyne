
from __future__ import annotations
from typing import List, Optional

def sma(vals: List[float], n: int) -> Optional[float]:
    if not vals or len(vals)<n: return None
    return sum(vals[-n:])/n

def atr(ohlcv: List[List[float]], n: int = 14) -> Optional[float]:
    if not ohlcv or len(ohlcv)<n+1: return None
    trs = []
    prev_close = ohlcv[-n-1][4]
    for row in ohlcv[-n:]:
        high, low, close = row[2], row[3], row[4]
        tr = max(high-low, abs(high-prev_close), abs(low-prev_close))
        trs.append(tr)
        prev_close = close
    return sum(trs)/len(trs) if trs else None

def trend_up(ohlcv: List[List[float]], ma_n: int = 50) -> Optional[bool]:
    closes = [r[4] for r in ohlcv] if ohlcv else []
    m = sma(closes, ma_n)
    if m is None: return None
    return closes[-1] > m

def trend_down(ohlcv: List[List[float]], ma_n: int = 50) -> Optional[bool]:
    closes = [r[4] for r in ohlcv] if ohlcv else []
    m = sma(closes, ma_n)
    if m is None: return None
    return closes[-1] < m
