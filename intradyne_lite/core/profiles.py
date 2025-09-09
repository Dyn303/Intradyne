
from __future__ import annotations
from typing import Dict, Any
import os, yaml

DEFAULTS = {
  "scalper": {"trend_filter": True, "sentiment_gate": True, "min_sentiment": 0.0, "atr_mult": 1.5, "risk_per_trade_pct": 0.005, "daily_max_loss_pct": 0.02, "timeframe": "5m", "ma_n": 50},
  "swing":   {"trend_filter": True, "sentiment_gate": False, "min_sentiment": -0.2, "atr_mult": 2.5, "risk_per_trade_pct": 0.01, "daily_max_loss_pct": 0.03, "timeframe": "1h", "ma_n": 100},
  "hybrid":  {"trend_filter": True, "sentiment_gate": True, "min_sentiment": -0.1, "atr_mult": 2.0, "risk_per_trade_pct": 0.008, "daily_max_loss_pct": 0.025, "timeframe": "15m", "ma_n": 50},
}

def load_profiles(path: str | None = None) -> Dict[str, Any]:
    p = path or os.getenv("PROFILES", "/app/profiles.yaml")
    if p and os.path.exists(p):
        with open(p,"r") as f:
            data = yaml.safe_load(f) or {}
        return {**DEFAULTS, **data}
    return DEFAULTS
