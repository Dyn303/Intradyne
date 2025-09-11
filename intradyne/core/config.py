"""Forwarder to canonical config in src/core/config.py.

Keeps public API: `load_settings()` and `Settings` type for compatibility.
"""

# ruff: noqa: F401
from src.core.config import Settings, load_settings
