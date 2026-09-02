# Pre-registration template

Copy this to `docs/<TEST_NAME>_PREREGISTRATION.md`, fill it in, and **commit it
before the test is written**. Delete the guidance in brackets as you go.

The two worked examples in this repo are
`docs/CROSS_SECTIONAL_PREREGISTRATION.md` and
`docs/CROSS_SECTIONAL_V2_PREREGISTRATION.md`. The second is the better model:
it opens by stating the defect in the first, which is the shape a follow-up
test should take.

Why this exists, in the words of the document that first used it:

> **Committed before the test was run.** That is the only property that makes
> this document worth anything. Criteria written after seeing results are not
> criteria, they are a story.

See `docs/STRATEGY_RESEARCH_FRAMEWORK.md` for the rules this template
operationalises; each section below names the amendment it satisfies.

---

# <Test name>: pre-registration

**Committed before the test was written or run.**

[One paragraph: what question this answers, and why it is worth running now.
If it follows a previous test, state that test's defect and what changes --
a two-column table of *before* and *after* is the clearest form. A
better-powered version of a flawed test is a new test, not a correction.]

## Feasibility

[Amendment A1. Before anything else: the round-trip cost in the target market,
the plausible edge scale at the intended horizon, and the ratio between them.
If costs are a large multiple of the effect you are looking for, say so and
stop here -- that is a complete and legitimate outcome for this document.

Use `round_trip_cost_pct()` and `breakeven_win_rate()` from
`src/intradyne/backtester/costs.py`.]

## Universe

[Amendment A3. How the universe is constructed, point-in-time.

- Source and the script that builds it.
- Survivorship: state explicitly that names which later delisted stay in every
  snapshot they belonged to, and how their loss is taken.
- Liquidity: judged over a window ending at each rebalance date, never today.
- Any size floor, and the date that floor fixes as the start. The start date
  should be *derived* from a stated rule, not chosen by inspection.
- For an equity universe, the compliance screen is itself point-in-time --
  financial ratios move quarterly, so state which vintage of data is used at
  each rebalance and how purification is costed.

If there is a primary and a secondary universe, say which is which **and what
the secondary can and cannot do**. If the primary fails, a less powerful
secondary cannot rescue it.]

## Signals

[Fixed count, listed. Adding one after seeing results invalidates the null.

State where each comes from -- a published specification carries no search
penalty; one you chose does. Say which is which.]

1.
2.
3.

## Method

[How positions are formed and rebalanced. Long-only, spot.

Amendment B1: exit mechanics are shared across all candidates, so they differ
only in when they enter.

Amendment B2: state the *realised* holding period you expect to observe, and
commit to reporting it next to the configured one. A holding parameter is
usually a maximum, not a measurement.]

**Costs:** [bps, on realised turnover rather than assumed. State the per-day
equivalent at the intended horizon -- that number is usually the whole
question.]

**Benchmark:** [Amendment B4. Random entry with matched trade count and
geometry, or the equal-weighted universe. Every result is excess over this,
never over zero. Say why this benchmark carries the same drift and the same
survivorship composition as the strategy.]

**Null:** [Amendment C1. Best-of-N at the 95th percentile, where N is every
candidate that touches this data -- across every generation, not just this
batch. Built from each candidate's own geometry and trade count, not once and
reused.]

**Significance:** [Amendment C2. Computed on the period series, not on pooled
trades. State the mean pairwise correlation of the universe and the effective
number of independent assets it implies. Instrument count is not sample
breadth.]

**Validation:** [Amendment C5. Walk-forward, selecting on each fold and
measuring on the next, with a purge and embargo around the boundary if labels
are built from forward returns. The reported number is what the procedure
earns, not what the best candidate earned in hindsight.]

## Criteria

[All must hold. Any failure is a negative result. Four is a good number; the
worked examples use these.]

1. Excess over the benchmark is **> 0**
2. Excess clears the **best-of-N null threshold**
3. Annualised Sharpe of the excess is **>= [X]**
4. Excess is positive in a **majority of walk-forward folds** ([N] folds)

### Why [X]

[Amendment A2. Derive the threshold from the available history rather than
choosing it. `t ~= Sharpe * sqrt(years)`, so with [Y] years available, 2 sigma
requires Sharpe >= [X].

State plainly that this is a property of the data: below it, a real edge and no
edge are indistinguishable with the history that exists. More frequent
rebalancing does not help -- observation count is not calendar time.

If the minimum detectable effect exceeds the effect you are looking for, this
test cannot answer the question. Say so and do not run it.]

## Reporting

[Amendment C3. Committed in advance, because these are the outputs that catch
a result which is really one name:

- Per-instrument contribution for every reported figure.
- The worst leave-one-out result.
- Ranking restricted to candidates that cleared the sample-size floor.

Amendment D1: the harness falsification check -- random input must earn
approximately zero -- and the test that pins it.]

## Commitments

- The **first** run is the result. No re-running with adjusted windows, slice
  widths or floors until something passes.
- [N] signals, [M] horizons, [K] universes. That is [N*M*K] cells, and the null
  accounts for them. No cell is added afterwards.
- A negative result is written up with the same detail a positive one would get.
- If a bug is found later, the fix is reported with both the before and after
  numbers, never a silent re-run.
- [Amendment D3, the stop rule. How many approaches this programme gets, and
  what ends it. If this is the last test, say so here: *"Whatever it shows, the
  search stops."* That sentence is what makes a question closeable.]

## What a positive result would and would not mean

[Guard against over-reading in advance, while it is still cheap.

It **would** mean: [the narrow claim the test actually supports].

It would **not** mean: [tradeability under the compliance screen, if that is a
separate test; persistence; deployability]. The honest next step after a
positive is forward paper measurement, not deployment -- and flipping
`STRATEGY_EDGE_DEMONSTRATED` in `src/intradyne/core/config.py` requires the
measurement, not this document.]

## Prior

[State your expectation and why. A stated prior makes a surprising result
easier to interpret and a confirming one harder to over-claim.

If the prior is low, say what running it buys anyway -- usually converting a
caveat into an answer, which is worth doing.]
