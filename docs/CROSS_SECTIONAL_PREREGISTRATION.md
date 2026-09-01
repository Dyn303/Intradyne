# Cross-sectional test: pre-registration

**Committed before the test was run.** That is the only property that makes
this document worth anything. Criteria written after seeing results are not
criteria, they are a story.

The reason for the formality is specific rather than ceremonial. Over the
course of this work, four separate apparently-profitable results dissolved
under inspection — a liquidation priced off an equity curve, a position cap
that bounded orders instead of positions, a stop recomputed from the latest
entry, and a maker mode that posted exits passively. A screen of fifty
signals then produced a confident "top 5" that was pure selection bias. The
common thread is that a plausible number arrived and nobody had written down
in advance what would have counted as failure.

## The question

Everything measured so far asked *when to enter one instrument*. This asks
*which instrument to hold*. It is a different question with different
economics: at a monthly rebalance a 14 bps round trip is a rounding error,
where at a two-minute horizon it was 28x the measured edge.

## Universe

Point-in-time, built by `scripts/point_in_time_universe.py`. Membership is
recomputed at each rebalance date from data available on that date only.

- **Survivorship is included.** 30% of spot USDT pairs Binance has ever
  listed are dead. A name that later died remains in every snapshot it
  belonged to, so a strategy holding it takes the loss it actually took.
- **Liquidity is judged at the date**, on median quote volume over a trailing
  window, never from today's volume.
- Leveraged tokens are excluded — they are derivatives in a spot wrapper.
- The Shariah screen (`docs/UNIVERSE_SCREENING.md`) is applied on top. Which
  categories pass is a scholarly ruling, not a modelling choice, and the
  screened list is fixed **before** the test runs.

## Method

Rank the universe on a signal, hold the top decile equal-weighted, rebalance
monthly. Signals to be tested:

1. trailing 1-month return
2. trailing 3-month return
3. trailing 6-month return
4. trailing 12-month return
5. short-term reversal (trailing 1-week return, inverted)
6. volatility-scaled 3-month momentum
7. downside-volatility rank
8. volume trend

## Controls

**Benchmark: equal-weight the whole universe.** Every result is reported as
*excess over that benchmark*, not over zero. Over months a long-only rule in
crypto earns whatever the market did, which dwarfs costs, so beating zero
proves nothing. The benchmark also carries identical survivorship
composition, so the excess is not flattered by it either.

**Null threshold.** With eight signals tested, the best will look good by
chance. Random portfolios with matched size and turnover give the
distribution of "best of N under no edge"; the 95th percentile is the bar.

**Walk-forward.** Selection happens on each fold and is measured on the next.
The reported number is what the *procedure* earns, not what the best signal
earned in hindsight.

**Costs.** 14 bps per round trip applied to realised turnover, not assumed.

## Criteria

All four must hold. Any failure is a negative result.

1. Excess over the equal-weight benchmark is **> 0**
2. Excess clears the **best-of-N null threshold**
3. Annualised Sharpe of the excess is **>= 0.8**
4. Excess is positive in a **majority of walk-forward folds**

### Why 0.8

Strategy-level detection follows `t ~= Sharpe * sqrt(years)`. The universe is
complete from 2020-10, so roughly 5.9 years are available, and 2 sigma
therefore needs Sharpe >= 0.82. This is a property of the available history,
not a preference: below that, a real edge and no edge are indistinguishable
with the data that exists, and more frequent rebalancing does not help —
observation count is not calendar time.

Published crypto cross-sectional momentum claims Sharpe 0.5–1.0, so the bar
sits at the edge of what is plausible. That is the honest prior going in.

## Commitments

- The **first** run is the result. No re-running with adjusted parameters,
  windows, or decile widths until something passes.
- Signals are the eight listed above. Adding a ninth after seeing results
  makes the null threshold wrong and the whole exercise meaningless.
- A negative result is written up in `MIGRATION.md` with the same detail a
  positive one would get.
- If a bug is found after the fact, the fix is reported alongside both the
  before and after numbers, never a silent re-run.

## Known limitations

- **Effective breadth is below headline breadth.** The names most likely to
  survive Shariah screening are L1/L2 infrastructure, the most mutually
  correlated group in crypto. Cross-sectional selection feeds on dispersion
  *between* names; a basket that moves together supplies less of it than the
  count suggests.
- **A negative result closes this version of the question, not the general
  one.** It would say cross-sectional selection does not work on a
  Shariah-screened basket of large-cap crypto over 2020–2026. It would not
  say the effect is absent in crypto at large.
- **~70 monthly rebalances is a small sample.** This is the same constraint
  that made the 8-hour horizon result untrustworthy, and it has not gone
  away — it is why criterion 3 is set where it is.
