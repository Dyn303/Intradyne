
from __future__ import annotations
from typing import Dict, Optional
import json, os

REG_PATH = os.getenv("SENTIMENT_PATH", "/app/data/sentiment/scores.json")

def _load() -> Dict[str, float]:
    try:
        with open(REG_PATH,"r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(d: Dict[str, float]):
    os.makedirs(os.path.dirname(REG_PATH), exist_ok=True)
    with open(REG_PATH,"w") as f:
        json.dump(d,f)

def set_score(symbol: str, score: float):
    d = _load()
    d[symbol] = float(score)
    _save(d)

def get_score(symbol: str) -> Optional[float]:
    return _load().get(symbol)

def bias_allow_long(symbol: str, min_score: float = -1.0) -> bool:
    s = get_score(symbol)
    if s is None: return True
    return s >= min_score
