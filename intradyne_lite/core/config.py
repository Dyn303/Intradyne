
import os, yaml

def load_config(path: str | None = None):
    p = path or os.getenv("CONFIG","/app/config.yaml")
    if os.path.exists(p):
        with open(p,"r") as f:
            return yaml.safe_load(f) or {}
    # sensible defaults
    return {
        "risk": {"capital": 10000},
        "storage": {"sqlite_path": "/app/data/trades.sqlite"},
        "accounts": []
    }
