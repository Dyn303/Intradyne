# Cross-sectional test v2: pre-registration

**Committed before the test was written or run.** That is the only property
that makes this document worth anything.

This is the *one* remaining crypto test. It exists because the first
cross-sectional test had a defect I recorded at the time rather than quietly
fixing: it fixed a top-decile portfolio but set no floor on universe size, so
it frequently held **3 names**. Random selection alone then had a standard
deviation of 1.74% per period, which is why the best-of-N null sat at +4.6%.
That test could not distinguish "no edge" from "an edge too small to see
through 3-name noise".

After this runs, crypto is answered either way and the search stops. No eighth
approach, no re-run with adjusted parameters.

## What was wrong, and what changes

| | v1 | v2 |
|---|---|---|
| universe | 103 unflagged names, median **37** | full universe, median **292** |
| slice | top 10% -> often **3 names** | top 20%, floor of **15 names** |
| universe floor | none | **>= 30 names** to score a period |
| start | first data (2 names) | **2019-11-05**, first date with >= 30 |
| horizon | 30 days | **30 and 90 days** |
| periods | 84 | ~84 monthly, ~28 quarterly |

The start date is fixed by the >= 30 rule, not chosen by inspection.

## Universe

Point-in-time, from `docs/universe_timeline.json`, built by
`scripts/point_in_time_universe.py`. Survivorship included: a name that later
delisted stays in every snapshot it belonged to and its loss is taken at its
last traded price. Liquidity was judged at each rebalance date, never from
today.

**Primary test: the full universe.** This answers the general question -- does
cross-sectional selection work in crypto at all -- with maximum statistical
power. It ignores the Shariah screen deliberately, because a test that cannot
detect an effect cannot inform a compliance decision either.

**Secondary test: the 103 unflagged names.** This answers whether any effect
found is tradeable under the screen. It is strictly less powerful than the
primary, so if the primary fails the secondary cannot rescue it -- it is
reported for completeness, not as a second chance.

## Signals

Fixed at five. Adding a sixth after seeing results invalidates the null.

1. trailing 1-month return (momentum)
2. trailing 3-month return
3. trailing 1-week return, inverted (short-term reversal)
4. 3-month return divided by its volatility (risk-adjusted momentum)
5. negative 90-day downside deviation (defensive)

These are the subset of the v1 signals that the crypto literature actually
points at, plus the volatility-scaled variant. The 6- and 12-month formations
are dropped: they were outside the window the literature identifies and were
the weakest performers.

## Method

Rank the universe, hold the top 20% equal-weighted with a floor of 15 names,
rebalance every 30 or 90 days. Long-only, spot.

**Costs:** 14 bps taker on realised turnover. At a quarterly horizon this is
roughly 0.5 bps per day, which is the entire point of testing here.

**Benchmark:** equal-weight the whole universe. Every result is excess over
that, never over zero -- a long-only rule in crypto earns whatever the market
did, and the benchmark carries identical drift and identical survivorship
composition.

**Null:** random portfolios of matched size and turnover, best-of-5 to match
the number of signals, 95th percentile.

**Significance:** the t-statistic is computed on the period series, not on
pooled trades. Crypto instruments correlate at 0.54 intraday; treating
correlated holdings as independent observations overstated an earlier result
threefold.

## Criteria

All four must hold. Any failure is a negative result.

1. Excess over the equal-weight benchmark is **> 0**
2. Excess clears the **best-of-5 null threshold**
3. Annualised Sharpe of the excess is **>= 0.8**
4. Excess positive in a **majority of walk-forward folds** (5 folds)

### Why 0.8

`t ~= Sharpe * sqrt(years)`. The universe supports ~6.8 years from 2019-11, so
2 sigma requires Sharpe >= 0.77. This is a property of the available history,
not a preference: below it, a real edge and no edge are indistinguishable with
the data that exists.

## Commitments

- The **first** run is the result. No re-running with adjusted windows, slice
  widths, or floors.
- Five signals, two horizons, two universes. That is ten cells, and the null
  accounts for the signal count. No cell is added afterwards.
- A negative result is written into MIGRATION.md with the same detail a
  positive one would get.
- If a bug is found later, the fix is reported with both the before and after
  numbers, never a silent re-run.
- **This is the last crypto test.** Whatever it shows, the search stops.

## What a positive result would and would not mean

It would mean cross-sectional selection shows an effect in crypto at
monthly-to-quarterly horizons, in a universe including the names that died,
net of realistic costs, beyond what selecting at random would produce.

It would **not** mean the effect is tradeable under the Shariah screen -- that
is the secondary test -- nor that it will persist. The honest next step after a
positive would be forward paper measurement, not deployment.

## Prior

Low. The v1 test found seven of eight signals underperforming equal-weight,
several by wide margins, against a benchmark sharing their drift and
survivorship. That is not the shape of an edge hidden under noise. The
correction here is to power, and power does not create an effect that is
absent -- it only reveals one that was too small to see.

The value of running it is that it converts a caveat into an answer.
