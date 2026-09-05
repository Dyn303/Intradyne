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

**Control.** See Amendment 1 -- the registered control was wrong, and its own
abort condition is what caught it.

### Amendment 1 -- the control is a resampling null

Registered 2026-09-05, after the primary run aborted twice and **before any
result was read as a finding**.

The registered control was entry at a uniformly random bar. Its own abort
condition rejected it: the control's mean sat at |t| = 4.22 from zero, and 8.72
after an attempt to match it per day. The diagnostic was that the control's
*sign flipped with the signal it was paired with* -- positive alongside
breakout, negative alongside mean reversion -- because breakout fires on days
price is rising and mean reversion on days it is falling. The control was
measuring the day's direction rather than providing a baseline.

The deeper problem no random-bar control can fix: both signals select on a
**window extremum**, and an extremum is a biased sample of prices whenever
prints carry transient noise. A control entering at a typical bar does not
share that bias, so the comparison confounds "this signal predicts" with "an
extremum is not a typical price".

**The control is now a resampling null.** Hourly log returns are shuffled
independently within each symbol, prices are rebuilt from the shuffled returns,
and the *identical* signal and measurement are run on that series. Repeated
B = 200 times to give a null distribution of the edge, against which the real
edge becomes an empirical p-value.

This is the right null because it destroys predictability while preserving both
the return distribution and the entire selection mechanism. If buying a 12-hour
low is mechanically profitable regardless of real structure, the shuffled series
shows it too and the real edge sits inside the null distribution.

*Stated limitation:* an IID shuffle also destroys volatility clustering, which
changes how often extrema occur and how extreme they are. The null is therefore
not exactly matched on clustering. A block bootstrap would preserve clustering
but would also preserve part of the 4-hour predictability under test, which is
the worse error.

**The abort becomes:** a configuration fails if its real edge falls inside the
central 95% of its null distribution. There is no separate control-mean check,
because the null distribution now *is* the control and is centred by
construction.

**Unchanged:** eight tests, so a configuration passes only at empirical
p < 0.00625, and must still clear its own symbol's round-trip cost.

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

## Outcome — primary window, recorded 2026-09-05 before the hold-out was run

2022-09 → 2025-08, 26,304 hourly bars per symbol, resampling null B = 200.

| signal   | lb | hz | n      | edge   | null mean | null 95%         | p      | cost  | verdict |
|----------|---:|---:|-------:|-------:|----------:|------------------|-------:|------:|---------|
| breakout | 12 |  4 | 11,454 | +2.47  | +3.20     | [+0.83, +5.46]   | 0.7313 | 15.15 | no      |
| breakout | 12 |  8 |  8,196 | +7.93  | +6.64     | [+2.62, +9.86]   | 0.2388 | 15.15 | no      |
| breakout | 48 |  4 |  5,050 | +7.87  | +3.26     | [−0.19, +6.63]   | 0.0149 | 15.15 | no      |
| breakout | 48 |  8 |  3,657 | **+17.86** | +6.46 | [+0.00, +12.32]  | **0.0050** | 15.15 | **PASS** |
| meanrev  | 12 |  4 | 10,995 | +7.94  | +3.49     | [+1.22, +5.89]   | 0.0050 | 15.15 | p only  |
| meanrev  | 12 |  8 |  7,996 | +9.33  | +6.87     | [+3.12, +10.92]  | 0.1393 | 15.15 | no      |
| meanrev  | 48 |  4 |  4,702 | +4.75  | +3.42     | [−0.59, +7.43]   | 0.2537 | 15.15 | no      |
| meanrev  | 48 |  8 |  3,445 | +1.99  | +6.89     | [−0.58, +12.95]  | 0.9154 | 15.15 | no      |

**One of eight passed:** breakout, 48h lookback, 8h horizon.

**What Amendment 1 removed.** Under the original random-bar control the four
mean-reversion configurations all "passed" with edges of +37 to +92 bps at
t between +10.8 and +13.1. Against the resampling null every one of them falls
inside its own null distribution. That apparent edge was the period's upward
drift plus the arithmetic of selecting a window extremum, and the amended
control absorbs both. Recorded because a reader should be able to see what the
weaker null would have concluded.

**Three cautions on the survivor, written before the hold-out.**

1. *The p is at the resolution floor.* With B = 200 the smallest achievable
   value is 1/201 = 0.00498, so p = 0.0050 means zero null draws exceeded the
   edge. It clears the registered bar legitimately, but the number does not
   measure how strong the effect is — only that it is beyond what 200 draws
   could reach.
2. *The margin over cost is 2.71 bps.* It clears, and not by much.
3. *It is the noisiest cell.* Longest lookback and longest horizon give the
   fewest entries of the eight, 3,657 across six symbols over three years.

A single survivor at the boundary of a corrected threshold is the shape a false
positive takes. The hold-out decides it.

## Outcome — hold-out, and the verdict

2025-09 → 2026-07, 8,016 hourly bars per symbol, same null, same bar.

| signal   | lb | hz | n     | edge   | null 95%          | p      | verdict |
|----------|---:|---:|------:|-------:|-------------------|-------:|---------|
| breakout | 12 |  4 | 3,411 | −0.36  | [−7.83, −0.35]    | 0.9701 | no      |
| breakout | 12 |  8 | 2,434 | −3.61  | [−12.76, −1.60]   | 0.9005 | no      |
| breakout | 48 |  4 | 1,403 | −1.31  | [−10.74, +2.73]   | 0.8657 | no      |
| **breakout** | **48** | **8** | 1,006 | **−6.93** | [−18.17, +1.33] | 0.6269 | **no** |
| meanrev  | 12 |  4 | 3,634 | −2.31  | [−6.88, +0.11]    | 0.7761 | no      |
| meanrev  | 12 |  8 | 2,564 | −8.06  | [−12.04, −1.94]   | 0.4080 | no      |
| meanrev  | 48 |  4 | 1,652 | −3.83  | [−9.24, +1.13]    | 0.4279 | no      |
| meanrev  | 48 |  8 | 1,168 | −8.88  | [−14.61, +1.60]   | 0.3333 | no      |

**Zero of eight.** The primary's survivor did not merely weaken: **+17.86 →
−6.93**, a swing of 24.8 bps and a change of sign. That is what an in-sample
artefact looks like, and it is what the three cautions recorded above were
pointing at.

### Verdict: FAIL

Registered criterion: *"Primary passes, hold-out does not → FAIL, and reported
as an in-sample artefact."* Met exactly.

### Crypto closes

The bound set at the top of this document: *"This is one approach... If it
fails, crypto closes and does not reopen on a further reframing — the next
reopening requires a new instrument class, not a new angle on this one."*

It failed. Crypto is closed. This is the eleventh and last approach against the
asset class, and the condition was written before the answer was known.

### What was actually learned

Not "the signals are bad" — that was already known from ten prior approaches
and 1,649 live paper trades. What this settles is the **horizon defence**: the
argument that the previous ten failed only because a 2-minute holding period
made success arithmetically impossible. Extending to 4 and 8 hours, where the
typical move is 2.5× the round trip rather than 0.2×, does not produce an edge
either. The horizon was a real constraint and removing it was not sufficient.

That closes the last open line of argument for this asset class, which is why
the programme can close rather than merely pause.

### The methodological finding, which outlives the programme

Under the originally registered random-bar control, four configurations
"passed" with edges of +37 to +92 bps at t between +10.8 and +13.1 — results
that would have justified real money. Every one was the period's drift plus the
arithmetic of selecting a window extremum. The abort condition caught it, the
resampling null removed it, and the hold-out would have caught it again.

Three independent safeguards were needed to stop one false positive. Any future
programme against any instrument should assume the same.
