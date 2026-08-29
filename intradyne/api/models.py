"""Compatibility shim routing to src implementation.

This shim was missing, which made src/intradyne/api/app.py -- the module the
container actually serves -- un-importable whenever the root ``intradyne``
package shadows ``src/intradyne`` (as it does under pytest.ini's path order).
Removed along with the rest of the shim layer in MIGRATION.md phase 1.
"""

# ruff: noqa: F401, F403
from src.intradyne.api.models import *
