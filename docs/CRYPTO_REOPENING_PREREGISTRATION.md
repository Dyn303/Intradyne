# Crypto, reopened — cross-sectional relative strength

**Registered:** 2026-09-08, before any signal is scored against this panel.
**Status:** an override. Recorded as one.

## This reopens a closed programme, on a decision rather than on evidence

`docs/HORIZON_PREREGISTRATION.md` closed crypto and set the condition:

> This is one approach... If it fails, crypto closes and does not reopen on a
> further reframing — the next reopening requires a new instrument class, not a
> new angle on this one.

It failed. Zero of eight out of sample, the one survivor flipping from +17.86 to
−6.93 bps. That bound is being overridden by the operator's decision, not by
new evidence, and this document exists so the override is visible from the
record rather than absorbed into it.

**What is different this time, and it is not nothing.** All twelve prior
approaches asked a *time-series* question: does this instrument's own history
predict its own next move. This asks a *cross-sectional* one: given that
everything moves together, does relative strength between instruments persist?
That is a different mechanism, not a reframing of the same one, and it has
never been tested here.

**What is not different.** The prior remains low, the asset class remains the
one that produced twelve negatives, and effective breadth is 1.51 assets, which
no amount of data repairs.

## Disclosure

Already seen, so a reader can discount:

- Twelve prior crypto approaches, all negative, the last properly powered.
- 1,649 live paper trades at a 2-minute horizon: gross −0.61 bps, PF 0.86.
- Market-making economics on this panel: negative at every realistic quoted
  spread, 0 of 24 symbols positive.
- The cross-sectional harness was run on crypto once as a **validation** of the
  harness, at daily steps over 1.6 years, and produced nothing significant.
  That run used a shorter panel and is superseded, but it was seen.
- Per-symbol spreads and round-trip costs.

**Not seen:** no signal has been scored against the four-year 24-symbol panel
assembled for this test.

## The hypothesis

> **H.** Ranking the universe by trailing return and holding the extreme decile
> earns a gross return, in excess of the equal-weighted universe on the same
> date, that exceeds both a resampling null and the round-trip cost.

Excess over the universe, not absolute return. A long-only decile carries the
market, and measuring its absolute return measures market direction — the flaw
`cross_sectional.py` was corrected for after crypto validation exposed it.

## What is fixed, now

**Universe.** 24 USDT spot pairs, hourly bars from the Binance public archive,
2022-09 to 2026-08. Symbols enter the panel when they begin trading rather than
being backfilled: ARB has 1,226 days and SEI 1,081, and treating either as
present earlier would be the crypto analogue of survivorship bias.

**Horizon: 1 day only.** Not a choice of convenience — it is the only horizon
with usable power. Measured on this panel:

| horizon | windows | detectable at 80% power |
|---------|--------:|------------------------:|
| 1 day   |   1,429 |              **15.3 bps** |
| 1 week  |     204 |                 158.9 bps |
| 1 month |      47 |                 652.4 bps |

**Signals: two, two lookbacks each.** Momentum (hold the strongest decile) and
reversal (hold the weakest), at 5-day and 21-day lookbacks. Reversal is
momentum's sign-flipped twin: if one works and the other does not, the asymmetry
is evidence; if both "work", the harness is wrong.

**Four configurations, one horizon = four tests.** Bonferroni: **p < 0.0125**.
Fixed now.

**Control.** The resampling null in `cross_sectional.py`: returns shuffled
across names within each date, destroying the cross-sectional signal while
preserving each date's market move and the dispersion between names. This is
the null the horizon test arrived at after a random-entry control failed its
own abort check for not sharing the selection mechanism.

**Statistic.** Mean excess return per position in bps, date-clustered.

**Cost gate.** Two figures, both reported:

- **taker 15.0 bps** — the execution actually measured on this venue.
- **maker ~9.0 bps** — realistic at this horizon in a way it was not at two
  minutes. The 88% signal suppression that made maker execution useless there
  came from signals arriving faster than resting orders filled; at one
  rebalance a day with hours to fill, that does not apply.

**Hold-out.** Primary 2022-09 → 2025-08. Confirmation 2025-09 → 2026-08, not
examined until the primary result is written into this file.

## The power limitation, stated plainly

Detectable effect is 15.3 bps against a 15.0 bps taker cost. An edge with real
margin — 20 bps or more — is visible. An edge that merely scrapes past cost is
not, and this test would return a null for it.

Against the 9 bps maker cost there is genuine headroom, which is the stronger
version of the test and the reason both gates are reported.

## Criteria

| outcome | conclusion |
|---|---|
| ≥1 configuration beats its null at p < 0.0125, exceeds the taker cost, and repeats on the hold-out | **Pass.** Forward paper test, newly pre-registered. |
| Beats the null and exceeds maker cost but not taker cost | Conditional. Tradeable only with maker execution, whose fill rate must then be measured before anything further. |
| Beats the null but neither cost | Real and untradeable. Recorded. |
| No configuration clears the bar | **Fail.** |
| Primary passes, hold-out does not | **Fail**, reported as an in-sample artefact. |

**Abort.** If the null's own distribution does not bracket zero, the harness is
wrong rather than the market interesting — stop and fix before reading
anything. `sanity_check` and the null-centring check exist for this.

## The bound on this override

**One approach.** Everything above is fixed. If it fails, crypto closes and
this override is spent — no fourteenth attempt, no further reframing, and no
filter added afterwards to rescue a null result.

**No market filter.** A regime or volatility filter was considered and refused
on arithmetic: detectable effect scales as `1/√n`, so a filter trading half the
time takes 15.3 bps to 21.6 and puts the test back above its own cost gate.
Power is the binding constraint here, and a filter spends it to buy a search.
If the unfiltered test produces something marginal, a single pre-registered
filter becomes a legitimate follow-up on a live hypothesis. It is not a way to
find one.

## Commitments

- All four results reported, including the three that will not be the best.
- The outcome recorded here whatever it is.
- A pass earns a forward test and nothing else.

## Prior

Low. Twelve approaches have failed, breadth is 1.51 effective assets, and the
mechanism being tested is one where low breadth bites hardest — ranking is
least informative when everything moves together, which is precisely what a
0.648 mean correlation describes.

What justifies running it anyway is that it is a different question, it is
adequately powered for an edge worth having, and it costs nothing but time.
What does not justify it is expectation of success.

## Outcome — primary window, recorded before the hold-out was run

2022-09 → 2025-08, 1,096 daily steps, 22 symbols live at the start and 24 at
the end. Resampling null B = 200, bar p < 0.0125.

| configuration  | n     | dates | edge   | null 95%           | p      | verdict |
|----------------|------:|------:|-------:|--------------------|-------:|---------|
| reversal lb=5  | 2,180 | 1,090 | −7.00  | [−13.26, +13.09]   | 0.2736 | no      |
| reversal lb=21 | 2,148 | 1,074 | −0.72  | [−10.95, +13.69]   | 0.9353 | no      |
| **momentum lb=5**  | 2,180 | 1,090 | **+19.59** | [−11.14, +12.07] | **0.0050** | **PASS** |
| **momentum lb=21** | 2,148 | 1,074 | **+20.84** | [−11.45, +14.11] | **0.0050** | **PASS** |

Two of four pass, both above the 15.0 bps taker cost. All four nulls bracket
zero and no harness fault fired.

**The asymmetry is the encouraging part.** The registration said: *if one works
and the other does not, the asymmetry is evidence; if both "work", the harness
is wrong.* Momentum passes at both lookbacks and reversal fails at both, which
is the shape a real effect takes and not the shape a broken null takes.

**Four cautions, written before the hold-out is examined.**

1. *The p is at the resolution floor.* With B = 200 the smallest achievable
   value is 1/201 = 0.00498, so 0.0050 means no null draw reached the edge. It
   clears the registered bar legitimately, but the number measures reach rather
   than strength.
2. *The margin over cost is about 5 bps* — 19.6 and 20.8 against 15.0. Real,
   and not comfortable.
3. *The portfolio is two names.* A decile of 24 symbols rounds to k = 2, so
   each rebalance holds two coins. Any live version of this is far more
   concentrated than the word "decile" suggests.
4. *This is in-sample.* The horizon test's survivor showed +17.86 in the
   primary and −6.93 out of sample, a change of sign. Nothing here is a finding
   until the hold-out agrees.
