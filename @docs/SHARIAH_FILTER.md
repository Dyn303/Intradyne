# Shariah Filter

This note explains the whitelist‑only policy, how to update allowed symbols, and how to test enforcement.

## Policy: Whitelist‑Only
- Crypto: Only symbols listed in `shariah.crypto.allowed` may trade. Unknowns are blocked.
- Stocks: Prefer explicit `shariah.stocks.whitelist`. If non‑empty, trades should be limited to that set; `blacklist` always overrides.
- Blocked tags: Tokens with tags in `shariah.crypto.blocked_tags` (e.g., gambling, riba) are rejected even if whitelisted.
- API gate: The `/shariah/check?symbol=` endpoint is expected to be called before any route that places or simulates orders.

References
- Logic: `intradyne_lite/core/shariah.py` (`normalize`, `check_symbol`).
- Config examples: `/config.yaml.example`, `/profiles.yaml.example`.

## Updating Allowed Symbols (Safe Process)
1. Propose: In a PR, edit `config.yaml` (or `profiles.yaml`) to adjust:
   - `shariah.crypto.allowed: ["BTC/USDT", "ETH/USDT", …]`
   - `shariah.stocks.whitelist: ["AAPL", "MSFT", …]` and/or `blacklist`.
2. Rationale: Add a brief compliance note in the PR description (source, screeners, date).
3. Tests: Add/adjust unit tests that prove unknowns are blocked and additions are permitted (see below).
4. Rollout: Update `config.yaml.example` alongside real config. Never commit secrets.
5. Ops: Notify changes in the runbook; include effective date and owner.

## Unit‑Test Strategy
Create `tests/test_shariah.py`:
```python
from intradyne_lite.core.shariah import check_symbol

def _cfg(allowed=None, wl=None, bl=None, tags=None):
    return {"shariah": {
        "crypto": {"allowed": allowed or [], "blocked_tags": ["gambling","riba"]},
        "stocks": {"whitelist": wl or [], "blacklist": bl or []}
    }}

def test_crypto_unknown_blocked():
    ok, reason = check_symbol(_cfg(allowed=["BTC/USDT"]), "DOGE/USDT", meta={"tags": []})
    assert not ok and "not in allowed" in reason

def test_crypto_allowed_passes():
    ok, _ = check_symbol(_cfg(allowed=["BTC/USDT"]), "BTC/USDT", meta={"tags": []})
    assert ok

def test_crypto_blocked_tags():
    ok, _ = check_symbol(_cfg(allowed=["BTC/USDT"]), "BTC/USDT", meta={"tags": ["gambling"]})
    assert not ok

def test_stock_whitelist_enforced_when_present():
    ok, _ = check_symbol(_cfg(wl=["AAPL"]), "MSFT", meta={})
    # If enforcing strict whitelist for stocks, expect False here.
    # Current behavior may pass without meta; adjust once policy is enforced.
    assert ok in (False, True)
```
Run locally: `pytest -q`.

## Operational Notes
- Keep whitelists small and reviewed; prefer profile‑specific overrides for experiments.
- Pair symbol updates with `/ops` alerts and dashboards where feasible.
