# Stage 2 — Does the entry rule have an edge worth having?

**Registered:** 2026-09-05, before the confirmatory run.
**Gate for:** Stages 3–4 of `docs/PERFORMANCE_IMPROVEMENT_PLAN.md`.

## Disclosure, first

A pre-registration written after looking at data is worth less than one
written before, and this one is written after a great deal of looking. What is
already in view, so a reader can discount accordingly:

- 1,649 closed paper trades, all from 2026-09-04/05, gross mean **−0.606 bps**,
  sd 14.35, win rate 45.7%, profit factor 0.86.
- Per-symbol gross means spanning −1.26 to +2.07 bps, every one with |t| < 1.8.
- A maker-execution run that showed +3.20 bps at t = +2.31, **withdrawn** as a
  time-of-day artefact — it ran overnight against a daytime control and had
  half the control's dispersion.
- A concurrent maker/taker A/B currently running, at n ≈ 10 per arm.

This document therefore cannot pre-register the *exploratory* question. It
registers the confirmatory one, and states plainly which parts are already
settled by the above and which are not.

## Why this is the gate

`PERFORMANCE_IMPROVEMENT_PLAN.md` establishes that win rate, profit factor and
drawdown are three views of one per-trade distribution, movable only by cost,
by gross edge, or by exit asymmetry — and that the third does nothing when the
second is zero. Stage 1 addresses cost and is bounded: the best achievable
round trip is about 9 bps all-maker on the tightest books.

So everything downstream depends on one quantity: **gross edge per trade**.
This is the gate that decides whether the programme continues.

## The hypothesis, stated so it can fail

> **H1.** The entry rule's gross edge per trade, measured against a
> random-entry control at matched timestamps and instruments, is at least as
> large as the round-trip cost it must pay — 9 bps at best-case maker
> execution, 14 bps at the taker execution currently running.

H1 is the programme-relevant claim. Not "is there an edge" but "is there an
edge worth having", because an edge below the cost floor makes no difference
to any decision anyone would take.

> **H2.** The entry rule's gross edge is greater than zero.

H2 is the interesting-but-not-decisive version. A 1 bps edge would be real
information and still would not clear a 9 bps floor. H2 matters only if the
answer to H1 is no *and* someone intends to pursue a cheaper execution venue
or a different instrument class.

## H1 is already answered, and the answer is no

This is the part that would be dishonest to defer to a future run.

Detecting a 9 bps effect at sd 14.35 needs **41 trades** for 80% power at a
two-sided 5% level. The existing sample is 1,649 — forty times over-powered
for the effect size that matters. The bound it places on gross edge:

| assumption                              | 95% interval on gross edge |
|-----------------------------------------|---------------------------:|
| naive, independent trades               |          [−1.30, **+0.09**] |
| SE × 3 for day-clustering               |          [−2.68, **+1.47**] |
| SE × 5                                  |          [−4.07, **+2.86**] |
| SE × 10 (stress test, not a measurement)|          [−7.53, **+6.32**] |

Under every one of these, including a clustering penalty far beyond anything
plausible, **the upper bound sits below the 9 bps floor**. H1 is rejected, and
it is rejected with room to spare rather than marginally.

The honest caveat: all 1,649 trades come from a single day, so day-clustering
cannot be *estimated*, only stressed. The table's lower rows are what-ifs. But
the conclusion survives them, which is the point of putting them there.

## What would overturn this

A conclusion that cannot be overturned is not a finding, so, specifically:

1. **A day effect of about −10 bps on 2026-09-04.** For a true 9 bps edge to
   produce the observed −0.6, that day would have to have been roughly 10 bps
   worse than average. If daily edge varies that much, a single day tells us
   nothing and multi-day data is mandatory. This is the only serious challenge
   to the reading above, and it is testable.
2. **A conditioning variable that separates the trades.** If the edge lives in
   a subset — one regime, one hour, one volatility band — the pooled mean can
   be zero while a tradeable subset is not. Any such split must be specified
   *before* it is measured, or it is the best-of-N problem the framework's
   Amendment A exists to prevent.
3. **A materially different cost floor.** If execution reached, say, 2 bps,
   the bar drops and the naive interval's upper end (+0.09) becomes the
   relevant comparison rather than +6.32. Nothing currently proposed gets
   there — 9 bps is already the all-maker, tightest-book case.

## The confirmatory run, if it is run

Run this only to test challenge (1), which is the live one.

**Design.** Forward-only, paper, the six-symbol universe, taker execution so
the sample matches the existing baseline. The strategy arm and a control arm
run concurrently on the same symbols against the same clock, with separate
portfolios and ledgers. The comparison is strategy minus control, which
removes any drift common to both.

### Amendment 1 — the control is random *time*, not random *side*

Registered 2026-09-05, **before the run**, on discovering the original was
both unimplementable and wrong.

*Unimplementable:* `forbid_shorting` refuses a sell beyond inventory at the
compliance layer, so half of a random-side control's entries would be blocked
rather than executed. The arm would silently become "long whenever the coin
flip said long", which is a random-time control with half the sample and an
undisclosed selection step.

*Wrong:* the strategy's claim is that at these moments price is about to rise
more than usual. The null is therefore that these moments are no different
from other moments, and the control that tests it enters long at **arbitrary
times** on the same instruments under the same exit rules. Random side tests
whether the chosen direction carries information, which is not a question a
long-only rule raises.

The control is `RandomEntryStrategy`: a fixed per-tick probability of
signalling a buy, ignoring price entirely, seeded per symbol so two symbols do
not fire in lockstep. It **replaces** the real strategies in its arm rather
than joining them, since a control running alongside what it controls for
measures nothing. Everything downstream -- sizing, stops, targets, the time
stop, the cost model, the spread filter -- is identical between arms, so the
only difference is when the entry happens.

`p = 0.004` per tick, which at a 1 s interval over six symbols is a few
hundred signals a day before position capacity refuses some, matching the live
strategy's order of magnitude. The realised counts are reported rather than
assumed equal, and the comparison is of means, which does not require them to
match.

**Statistic.** Difference in mean gross bps per trade, with a **day-clustered**
standard error — the cluster is the trading day, because that is the level at
which the challenge operates.

**Power.** The binding sample size is *days*, not trades, and the day-level
standard deviation is currently unknown because there is one day. So the run
has two phases: a pilot of **10 trading days** to estimate day-level sd, then a
power calculation from that estimate to fix the confirmatory length. The
confirmatory length is not chosen until the pilot's sd is in hand, and the
pilot's point estimate of the edge is not used to decide whether to continue.

## Criteria, fixed now

| outcome                                                | conclusion |
|--------------------------------------------------------|------------|
| day-clustered 95% upper bound < 9 bps                   | **H1 rejected.** Stages 3–4 are parameter-fitting on noise; the stop rule in `EQUITY_PROGRAMME_STOP_RULE.md` applies. |
| upper bound ≥ 9 bps and lower bound > 0                 | H1 survives. Proceed to Stage 3 with a fresh pre-registration. |
| upper bound ≥ 9 bps, lower bound ≤ 0                    | Underpowered. Extend by the pre-computed increment once, then stop regardless. |

**Abort conditions.** The run is abandoned, and the slot returned unspent,
if: the feed's `interval_s` exceeds 2.0 s for more than 5% of the run, since
tick-counted windows then mean something different from the baseline's; or
fewer than 30 trades per day are produced, making day-level means too noisy to
cluster on.

## Commitments

- No parameter is changed during the run. A change ends the run and starts a
  new one.
- No subset analysis that was not named in "What would overturn this" above.
- The result is recorded here, in this file, whatever it is — including
  "abandoned" and including a negative, following the precedent set by
  `APPROACH_1_PREREGISTRATION.md`, whose slot was returned unspent.
- The A/B currently running is a Stage 1 execution question and does **not**
  feed this gate. Its result cannot be used to justify continuing.

## Prior

Low. Ten crypto approaches have been tested and all were negative; the
measured intraday edge that motivated this programme was +0.49 bps against a
round trip 28–35× larger. The present sample says −0.61 bps. Nothing in the
current evidence suggests H1 survives, and this document exists mainly so that
the conclusion is reached by a rule fixed in advance rather than by fatigue.

The most likely honest outcome of Stage 2 is that it confirms what the
existing 1,649 trades already indicate, and the programme stops.
