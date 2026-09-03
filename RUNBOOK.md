# IntraDyne — Operations Runbook

Covers the system as it actually exists. The previous version documented a
predecessor: connector setup for Alpaca and IBKR, and endpoints
(`/ops/ping`, `/ops/test_connectors`, `/profiles/apply`, `/watcher/stop`,
`/analytics/latency`) that this codebase has never had.

**Live trading is currently barred at startup.** See "Going live" below.

---

## 1. What runs

One container serves the API and, when enabled, hosts the trading loop in the
same process — one portfolio, one ledger, one pre-trade gate.

```
uvicorn intradyne.api.app:app        # the whole system
```

`ENGINE_ENABLED=true` starts the trading loop inside it. Off by default.

## 2. Start and stop

```bash
docker compose -f deploy/docker-compose.prod.yml up -d
```

Required in production, or the container refuses to start:

| Variable            | Why it blocks startup                                  |
| ------------------- | ------------------------------------------------------ |
| `X_API_KEY`         | Auth is on by default in prod; without a key it fails closed |
| `FRONTEND_ORIGINS`  | A `*` origin list is refused in prod                   |
| `BITGET_API_KEY` / `_SECRET` / `_PASSPHRASE` | Broker settings validation |

## 3. Health and state

| Endpoint          | Use                                                     |
| ----------------- | ------------------------------------------------------- |
| `/healthz`        | Process is up                                           |
| `/readyz`         | Database reachable. Observes only; never creates        |
| `/version`        | Running version                                         |
| `/metrics`        | Prometheus exposition, including the safety gauges      |
| `/risk/status`    | Drawdown, VaR, breach count, halt state, thresholds     |
| `/portfolio`      | Balances and open positions                             |
| `/engine/status`  | Whether the loop is running, and on which symbols       |
| `/engine/state`   | Same portfolio the loop trades                          |
| `/ledger/tail`    | Recent explainability records                           |

All of these require the `X-API-Key` header when auth is enabled.

## 4. Halting trading

The halt is the primary operator control. It is checked inside the pre-trade
gate, so it stops **every** order path — API-submitted and strategy-generated —
and is enforced again at the live broker boundary.

```bash
curl -X POST localhost:8080/admin/halt \
  -H "X-API-Key: $X_API_KEY" -H "X-Admin-Secret: $ADMIN_SECRET" \
  -d '{"enabled": true}'
```

Confirm with `GET /risk/status` (`halted`, `halt_reason`). Every halt and every
refused order is written to the hash-chained ledger.

## 5. Alerts

`deploy/monitoring/prometheus-alerts/intradyne.yml`, group `intradyne-safety`:

| Alert                       | Meaning                                          |
| --------------------------- | ------------------------------------------------ |
| `Trading_Halted`            | Kill switch or operator halt engaged             |
| `Unreconciled_Live_Orders`  | Submissions claimed but never completed — **verify on the venue** |
| `Drawdown_Approaching_Halt` | 30d drawdown reached `DD_WARN_PCT`               |
| `Drawdown_Halt_Threshold`   | Reached `DD_HALT_PCT`; the gate is halting        |
| `Guardrail_Breach_Rate`     | Breaches in 24h reached `KILL_SWITCH_BREACHES`   |
| `Live_Trading_Armed`        | Awareness: real funds are at risk                |

## 6. Verifying the audit trail

The ledger is append-only and hash-chained. To check it has not been altered:

```python
from intradyne.core.ledger import Ledger
ok, index, reason = Ledger(path="/app/data/explainability_ledger.jsonl").verify_chain()
```

`ok=False` names the first broken record. Investigate before trusting any
compliance claim based on it.

## 7. Exposure caps

Bound how much the system can transact, independent of the risk thresholds.
All in quote currency; `0` disables.

| Variable                   | Effect                                  |
| -------------------------- | --------------------------------------- |
| `MAX_ORDER_NOTIONAL`       | Largest single order                    |
| `MAX_SYMBOL_NOTIONAL_24H`  | Rolling 24h per symbol                  |
| `MAX_DAILY_NOTIONAL`       | Rolling 24h across all symbols          |

A cap that cannot be evaluated (an order with no price) is refused, not passed.

## 8. Going live — not yet enabled

Live trading is refused at startup by `assert_live_trading_gate()`. This is a
code gate (`LIVE_TRADING_GATE_OPEN` in `intradyne/core/config.py`), not an
environment variable, so opening it leaves a reviewable commit.

Built and tested:

- Triple gate: `MODE=live` **and** `LIVE_TRADING_ENABLED=true` **and** not halted
- Idempotency keys claimed before the venue is contacted and sent as
  `clientOrderId`, so a crash mid-submit cannot become a second real order
- Restart reconciliation: unresolved submissions halt trading rather than
  being guessed at in either direction
- Exposure caps (section 7)
- Safety alerting (section 5)

**Still required before opening the gate — none of it can be done from a
development machine:**

1. **Testnet soak.** Run against Bitget testnet with real keys for a
   sustained period. Confirm fills reconcile, the ledger chain verifies, and
   equity tracks the venue.
2. **Confirm alerts actually deliver.** The rules exist; that a page reaches a
   human has never been tested. Fire `Trading_Halted` deliberately and verify
   receipt.
3. **Set the exposure caps.** They default to `0` (disabled). Live trading
   without them means the only bound on transacted volume is the risk
   thresholds.

   `.env.example` now ships worked starting values with the derivation, for a
   nominal 10,000 equity at `MAX_POS_PCT=0.015`. Scale them linearly to your
   own equity. The point to carry across: **these cap turnover, not exposure**
   — twenty round trips a day at 1.5% transacts 60% of equity in daily notional
   while never holding more than 1.5% at once, so a daily cap set from
   position-size intuition halts the engine within the hour. The caps therefore
   sit *above* expected traffic; a cap that binds in normal operation is an
   outage, not a backstop, and repeated breaches trip `KILL_SWITCH_BREACHES`.

   Revise the turnover assumption after the soak, when it is measured rather
   than guessed. `tests/test_exposure_cap_defaults.py` pins the example against
   `MAX_POS_PCT` so the two cannot drift apart silently.
4. **Rehearse the halt** under live conditions and time it.
5. **Establish an edge after costs — the remaining blocker.** Every backtest
   summary now reports the measured win rate beside the breakeven it must
   clear, and `scripts/edge_report.py` scores runs. At the shipped defaults
   breakeven is ~88% (all-taker) or ~68% (all-maker). A run must land on
   `clears_with_margin`, not `marginal`.

   ```bash
   python scripts/edge_report.py --breakeven
   python scripts/edge_report.py --runs artifacts/backtests
   ```
6. ~~Obtain a Shariah ruling on the strategy itself.~~ **Settled.** A
   scholarly ruling has cleared high-frequency scalping as permissible for
   this system. The structural rules remain enforced in code — spot only,
   long only, no leverage, whitelist.

Then set `LIVE_TRADING_GATE_OPEN = True`, in its own commit, and start with
`MODE=live LIVE_TRADING_ENABLED=true` and caps configured.

## 9. Common problems

| Symptom                              | Cause                                            |
| ------------------------------------ | ------------------------------------------------ |
| Container exits immediately          | A required variable is unset (section 2), or live trading is armed |
| Every request 401                    | Missing or wrong `X-API-Key`                     |
| Orders refused with `halt`           | Halt engaged; check `/risk/status` for the reason |
| Orders refused, `inventory unknown`  | A sell with no known position — long-only fails closed |
| `/engine/status` shows `running: false` with `enabled: true` | Loop crashed; supervisor restarts it, check logs |
| Loop logs `could not load ... markets` | Venue unreachable; falls back to the unfiltered whitelist |

## The engine refuses to start: "no edge has been demonstrated"

This is intentional, not a misconfiguration.

Measurement across 50 signals, 8 families, 943 days and two instruments puts
the best entry signal at roughly **0.5 bps** against a round-trip cost of
**4-14 bps**. The shipped strategy loses about 13 bps per round trip, so the
loop is gated shut at boot rather than quietly trading it. Full working in the
"Fifty signals" and "Months of data" sections of MIGRATION.md.

Three ways forward, depending on what you actually want:

**Run the API without the trading loop** — the normal choice for serving
endpoints, metrics and the ledger:

```bash
ENGINE_ENABLED=false
```

**Run the loop anyway, for research** — paper mode only. This is how a
replacement strategy gets validated, so it is deliberately permitted; it logs
a warning on every start:

```bash
ENGINE_ENABLED=true ACKNOWLEDGE_NO_EDGE=true MODE=paper
```

**Declare an edge** — flip `STRATEGY_EDGE_DEMONSTRATED` in
`src/intradyne/core/config.py`. Like the live-trading gate this is a code
change rather than an environment variable, so opening it leaves a reviewable
commit. Do not flip it without the measurement that justifies it: a win rate
on its own is not evidence, and `scripts/strategy_months.py` exists to produce
the walk-forward number that is.

Note that acknowledging the lack of edge does **not** open live trading.
`LIVE_TRADING_GATE_OPEN` is a separate, non-overridable check.
