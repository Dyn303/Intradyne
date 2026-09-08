# Research log

Every recorded research run, newest first. Generated from `docs/research_runs.jsonl` by
`python scripts/research_ledger.py --write`. **Do not edit by hand** --
regenerating overwrites this file.

**Chain intact.** 1 run verified.

## Verdicts

| verdict | runs |
| --- | ---: |
| precondition_failure | 1 |

1 of 1 run carries a provenance caveat; see the notes on each.

## Runs

| date | verdict | script | commit | pre-registration |
| --- | --- | --- | --- | --- |
| 2026-09-03 | precondition_failure † | `approach_1_ranking_fetch` | `68d41f3` | APPROACH_1_PREREGISTRATION.md |

† provenance reconstructed after the fact rather than captured at
runtime, which is weaker evidence than a contemporaneous record.

## Detail

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

