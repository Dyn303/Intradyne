# Risk Guardrails

This document defines risk controls and how they gate trade placement. It serves as an implementation guide and test reference.

## Definitions
- 30‑day Drawdown (DD): rolling peak-to-trough equity loss over last 30 days.
  - Warn at ≥15%; Halt at ≥20% until manual override.
- Flash‑crash: pause trading on any symbol if price drops >30% within 1 hour.
- Kill‑switch: halt all trading if ≥3 guardrail breaches occur within 24h.
- VaR step‑down: if 1‑day VaR >5% of equity, reduce risk/profile (e.g., halve position sizing) until VaR ≤5%.

## Pseudocode
```python
# helpers (sketch)
now = utc_now()

 def dd_30d(equity_series):
     peak, dd = -inf, 0
     for t, eq in equity_series.last_days(30):
         peak = max(peak, eq)
         dd = max(dd, (peak - eq) / max(peak, 1e-9))
     return dd

 def flash_crash(symbol):
     p1h = price(symbol, now - 1h)
     pnow = price(symbol, now)
     return (p1h - pnow) / max(p1h, 1e-9) > 0.30

 def kill_switch_recent(breaches):
     return count(b for b in breaches if b.ts >= now-24h) >= 3

 def var_1d(equity_returns):
     return historical_var(equity_returns.last_days(30), alpha=0.95)  # 5% tail

 def gate_trade(req):
     # shariah/whitelist first
     if not is_whitelisted(req.symbol) or not shariah_ok(req.symbol):
         return Block(reason="compliance")

     eq30 = load_equity_series(days=30)
     breaches = load_recent_breaches(days=1)

     dd = dd_30d(eq30)
     if dd >= 0.20: return Halt("dd_halt")
     if dd >= 0.15: warn("dd_warn")

     if flash_crash(req.symbol): return Pause("flash_crash")

     if kill_switch_recent(breaches): return Halt("kill_switch")

     v = var_1d(daily_returns(eq30))
     if v > 0.05:
         req = req.step_down()  # reduce size/risk
         note("var_stepdown")

     return Allow(req)
```

## Logging and Ops Surfacing
- Every breach writes a JSON log line with `event="guardrail_breach"`, fields: `type`, `symbol`, `metric`, `value`, `threshold`, `action`, `ts`, `hash_prev` (for chain), `hash`.
- Persist breaches to SQLite table `guardrails(ts TEXT, type TEXT, symbol TEXT, metric REAL, threshold REAL, action TEXT, hash TEXT, prev TEXT)`.
- Notifications: route critical actions (Halt/Pause) via `intradyne_lite.core.notifier.notify` (Telegram/SMTP). Include runbook hint and last 3 metrics.
- Surfacing: expose `/ops/guardrails/recent` and `/ops/guardrails/status` for dashboards; return aggregated counts and latest DD/VaR.

## Config and Thresholds
- Env (suggested): `DD_WARN_PCT=0.15`, `DD_HALT_PCT=0.20`, `FLASH_CRASH_PCT=0.30`, `KILL_SWITCH_BREACHES=3`, `VAR_1D_MAX=0.05`.
- YAML: place under `risk:` in `config.yaml` with sane defaults; allow per‑profile overrides in `profiles.yaml`.

## Tests and References
- Config templates: `/.env.example`, `/config.yaml.example`, `/profiles.yaml.example`.
- API surface: `/ops/ping`, `/healthz`, and proposed `/ops/guardrails/*`.
- Suggested tests (to add): `tests/test_guardrails.py` for DD/flash‑crash/Kill‑switch/VaR; `tests/test_gate_trade.py` for end‑to‑end gating.
- Runbook: `/RUNBOOK.md` (add “Risk Breaches” SOP section).
```
Example breach log (JSON):
{"event":"guardrail_breach","type":"dd_halt","metric":0.206,"threshold":0.20,"action":"halt","ts":"2025-09-04T12:00:00Z","hash_prev":"…","hash":"…"}
```
