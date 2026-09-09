# Research log

Every recorded research run, newest first. Generated from `docs/research_runs.jsonl` by
`python scripts/research_ledger.py --write`. **Do not edit by hand** --
regenerating overwrites this file.

**Chain intact.** 3 runs verified.

## Verdicts

| verdict | runs |
| --- | ---: |
| precondition_failure | 1 |
| gate_requalified | 1 |
| precondition_lifted | 1 |

3 of 3 runs carry a provenance caveat; see the notes on each.

## Runs

| date | verdict | script | commit | pre-registration |
| --- | --- | --- | --- | --- |
| 2026-09-09 | gate_requalified ⚠ | `equity_feasibility` | `0f99e59c` | EQUITY_PROGRAMME_STOP_RULE.md |
| 2026-09-09 | precondition_lifted ⚠ | `delisted_series_check` | `8e91dc28` | APPROACH_1_PREREGISTRATION.md |
| 2026-09-03 | precondition_failure † | `approach_1_ranking_fetch` | `68d41f3` | APPROACH_1_PREREGISTRATION.md |

⚠ ran from a tree with uncommitted changes, so it cannot be
reproduced from its commit alone.

† provenance reconstructed after the fact rather than captured at
runtime, which is weaker evidence than a contemporaneous record.

## Detail

### 2026-09-09 — `equity_feasibility`

**Verdict:** gate_requalified

- run `88fc7183f977`
- commit `0f99e59c` — **dirty tree**, not reproducible from this commit alone
- pre-registration: [EQUITY_PROGRAMME_STOP_RULE.md](EQUITY_PROGRAMME_STOP_RULE.md) @ `ecaf79b`
- argv: `ecaf79b`

**Parameters**

- `assumed_half_spread_bps`: 1
- `cost_model`: max(assumed, one-cent tick floor) per name
- `interval`: 30min
- `names`: 13

**Result**

| field | value |
| --- | ---: |
| after_verdict | FAIL |
| after_worst_2min_ratio | 0.97 |
| band_under_10_names | 1 |
| before_flat_worst_2min_ratio | 1.38 |
| before_verdict | PASS |
| caveat | n=1 in the sub-$10 band; one name is not a band measurement |
| failing_detail | UAA $6.16, 18.53bps round trip vs 17.89bps move |
| failing_names | 1 |
| price_floor_usd | 6.16 |
| reading | a price floor on the tradeable universe, not infeasibility |
| universe_share_under_10_pct | 0.7 |
| worst_ratio_above_floor | 1.38 |

### 2026-09-09 — `delisted_series_check`

**Verdict:** precondition_lifted

- run `a34f7d21ace3`
- commit `8e91dc28` — **dirty tree**, not reproducible from this commit alone
- pre-registration: [APPROACH_1_PREREGISTRATION.md](APPROACH_1_PREREGISTRATION.md) @ `68d41f3`
- argv: `68d41f3`

**Parameters**

- `endpoints`: TIME_SERIES_DAILY(compact), TIME_SERIES_WEEKLY
- `provider`: alphavantage free tier
- `symbols`: ADVM,FXEN

**Result**

| field | value |
| --- | ---: |
| advm_daily_distinct_closes | 1 |
| advm_daily_zero_volume_bars | 100 |
| advm_weekly_bars | 550 |
| advm_weekly_close_high | 24.63 |
| advm_weekly_close_low | 0.57 |
| advm_weekly_distinct_closes | 414 |
| caveat | live API, so inputs carry no fingerprint; the committed script re-verifies rather than the record standing alone |
| fxen_daily | Invalid API call |
| fxen_weekly_bars | 843 |
| fxen_weekly_distinct_closes | 549 |
| fxen_weekly_zero_volume_bars | 0 |
| reproduction | scripts/delisted_series_check.py |
| slot_spent | no |

### 2026-09-03 — `approach_1_ranking_fetch`

**Verdict:** precondition_failure

- run `a016bca27938`
- commit `68d41f3`
- seed `20191101`
- pre-registration: [APPROACH_1_PREREGISTRATION.md](APPROACH_1_PREREGISTRATION.md) @ `42d5bbc`
- **† backfilled:** reconstructed from PR #37; this ledger did not exist when the run happened, so provenance is from the merged record rather than captured at runtime

**Parameters**

- `interval`: 30min
- `sample`: 120
- `universe`: 5590
- `window`: 2019-11..2019-12

**Result**

| field | value |
| --- | ---: |
| delisted | 37 |
| delisted_reachable | 2 |
| live | 83 |
| live_reachable | 59 |
| lost_to_delisting_pct | 29.2 |
| reachable | 61 |
| slot_spent | no |
| threshold_pct | 20 |

