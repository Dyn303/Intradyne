# Multi-hour horizon test — pre-registration

**Registered:** 2026-09-05, before any signal is scored against this data.
**Data:** 6 symbols × 47 months × 34,320 hourly bars, zero gaps, 3.9 years
(`data/horizon/bars/`, Binance public archive).

## First: is this allowed to exist?

`docs/EQUITY_PROGRAMME_STOP_RULE.md` records that crypto received ten
approaches, all negative, **"not because ten was chosen, but because nobody
said stop."** A closed programme that reopens whenever someone has a new idea
was never closed. So this document has to justify itself before it does
anything else.

**The case that this is not an eleventh approach.** The ten were searches for a
*signal* at a fixed, short holding period. This is not another signal. It is a
structural claim about the holding period itself, and it comes from a
measurement taken on 2026-09-05:

| hold        | typical move | round-trip cost | move ÷ cost |
|-------------|-------------:|----------------:|------------:|
| 2 min (all prior work) |    2.7 bps |          14 bps |  **0.2×** |
| 4 hours     |     34.5 bps |          14 bps |       2.5× |

At two minutes the price moves a fifth of what the trade costs. **A perfect
predictor loses money there** — it would capture 2.7 bps and pay 14. Every one
of the ten approaches was tested in a regime where none of them could have
worked regardless of merit, so their failure is not evidence about signals; it
is evidence about the horizon, and that is a different finding than the one
recorded against them.

**The case against.** This is exactly the reasoning that keeps a dead programme
alive: a new frame, a new dataset, one more look. The honest response is not to
refuse the test but to bound it in advance.

**The bound.** This is **one** approach, with everything below fixed before
measurement. If it fails, crypto closes and does not reopen on a further
reframing — the next reopening requires a new instrument class, not a new angle
on this one. This document is the record that the condition was set in advance.

## Disclosure

What has already been seen, so a reader can discount:

- 1,649 live paper trades at the 2-minute horizon: gross −0.61 bps, PF 0.86.
- The move-versus-cost table above, which motivated this test.
- Per-symbol spreads and round-trip costs (`docs/spread_measurements.json`).
- **No signal has been scored against the four-year hourly data.** It was
  fetched today and used only to count bars and estimate return dispersion for
  the power calculation below.

## The hypothesis

> At a multi-hour holding period, a pre-specified entry signal earns a gross
> return per trade that exceeds both a random-entry control and the round-trip
> cost at that horizon.

Both halves are required. Beating the control without clearing cost is a real
finding about markets and a useless one for trading; clearing cost without
beating the control means the drift did it, not the signal.

## What is fixed, now

**Horizons: 4h and 8h. Not 24h.** The 24-hour horizon needs ~12,228
non-overlapping windows for 80% power and 3.9 years supplies 2,429 — five times
short. It is not a fetch away: it needs roughly twenty years, and SOL and AVAX
did not exist before 2020. Excluded on feasibility, permanently for this
universe.

**Signals: two, two lookbacks each.**

| signal          | rule                                              | lookbacks |
|-----------------|---------------------------------------------------|-----------|
| breakout        | close is the maximum of the trailing window       | 12h, 48h  |
| mean reversion  | close is the minimum of the trailing window       | 12h, 48h  |

These are the hourly translations of `MomentumStrategy` and `MeanRevStrategy`.
No threshold tuning, no additional filters, no parameter search. Four signal
configurations × two horizons = **eight tests**.

**Control.** Entry at a uniformly random bar, same symbols, same horizons,
matched in count per symbol per horizon, seeded and recorded. The comparison is
signal minus control.

**Statistic.** Mean gross return per trade in bps, signal minus control, with a
**day-clustered** standard error. Overlapping windows are not treated as
independent: entries are sampled at horizon-length spacing.

**Multiple comparisons.** Eight tests, Bonferroni: α = 0.05/8 = 0.00625, so
**|t| > 2.73** is the bar, not 1.96. Fixed now so it cannot be relaxed later.

**Power at that bar.** The stricter α raises the required sample by ~1.62×:
4h needs 3,259 windows against 14,584 available; 8h needs 6,342 against 7,291.
Both clear, 8h with less room, which is stated rather than discovered later.

**Cost gate.** The round trip is `spread + 2 × slippage + 2 × taker` per
`docs/spread_measurements.json`: 14.00 bps on BTC to 15.34 on AVAX. A signal
passes only if its edge over control exceeds its own symbol's cost.

**Hold-out.** Fit nothing — every rule above is fixed — but confirm anyway.
Primary test on 2022-09 → 2025-08. Confirmation on 2025-09 → 2026-07, which is
not examined until the primary result is written down.

## Criteria

| outcome                                                              | conclusion |
|----------------------------------------------------------------------|------------|
| ≥1 configuration beats control by more than its cost, \|t\| > 2.73, and repeats on the hold-out | **Pass.** Proceed to a forward paper test at that horizon, newly pre-registered. |
| Beats control at \|t\| > 2.73 but by less than cost                    | Real but untradeable. Recorded as such. Crypto closes. |
| No configuration clears \|t\| > 2.73                                   | **Fail.** Crypto closes permanently under the bound above. |
| Primary passes, hold-out does not                                     | **Fail**, and reported as an in-sample artefact. |

**Abort.** If the control's own mean differs from zero at |t| > 2.73, the
harness is wrong rather than the market interesting — stop and fix it before
reading anything else.

## Commitments

- No signal, lookback, horizon, or threshold is added or changed after this
  file is committed. A change voids the run and requires a new registration.
- All eight results are reported, including the seven that will not be the
  best one.
- The outcome is recorded in this file whatever it is, following
  `APPROACH_1_PREREGISTRATION.md`, whose slot was returned unspent.
- A pass does **not** mean the strategy works. It means one hypothesis survived
  one test on historical bars, and earns a forward test — nothing more.

## Prior

Low, but higher than the 2-minute work deserved. Ten crypto approaches have
failed, and this is the eleventh look at the same asset class by any honest
accounting. What distinguishes it is that the previous ten were run where the
arithmetic made success impossible, and that this one is bounded in advance.

The most likely outcome remains that nothing clears, and the value of writing
this first is that the conclusion will then be reached by a rule rather than by
argument.
