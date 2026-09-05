# Slot 1 — Cross-sectional reversal in a Shariah-screened US equity universe

**Registered:** 2026-09-05, before any signal is scored against equity prices.
**Budget:** slot 1 of the four in `docs/EQUITY_PROGRAMME_STOP_RULE.md`.
**Status:** *not started* — the preconditions below are unmet.

## Preconditions, which must pass before the slot is spent

The framework treats a test that cannot be run as a precondition failure rather
than a negative result, and Approach 1's slot was returned unspent under
exactly that rule. Three things must be true first. **None is true today.**

**P1 — Prices joinable to the universe.** `docs/spus_universe_timeline.json`
identifies holdings by CUSIP, because N-PORT carries no ticker. A price source
must map CUSIP to a tradeable symbol *through renames and delistings*, or the
same 43%-style bias returns by the back door: names that changed ticker would
silently drop out. Free sources do not do this. QuantConnect's security master
and map files do, at a per-file download cost not yet priced.

**P2 — Spreads measured, not assumed.** The tick-size floor is `100 / price`
bps, so a $200 name has a 0.5 bp minimum spread against crypto's 14 bps — a
20-to-30-fold structural improvement, and the reason this universe is worth the
trouble. But a *floor* is not a spread. Real quoted spreads, and the depth
behind them, must be measured per name the way
`scripts/measure_spreads.py` did for crypto, and committed alongside the
result.

**P3 — Enough of the universe reachable.** Approach 1 aborted when 29.2% of a
sample could not be fetched against a 20% ceiling. The same ceiling applies:
if more than 20% of the point-in-time universe cannot be priced, the panel is
not survivorship-honest and the slot is not spent.

## Why this hypothesis

Two facts shape it, both measured rather than assumed.

*Cost is no longer the binding constraint.* Crypto's round trip was 14–25 bps
against typical moves of 2.7 bps at two minutes. Here the tick floor is under
1 bp on most of the universe. The cost side of gate A1 stops being the reason
nothing works, which is precisely what could not be said of the last eleven
approaches.

*Breadth is the binding constraint instead.* The universe holds 180–215 names
per quarter and 322 across six years, but concentration has risen sharply:
weight-effective names (1/HHI) fell from **33.9 in 2020 to 19.2 in 2026**, with
the top ten now 57.9% of the fund. And weight concentration is the *optimistic*
measure — correlation-based effective breadth for equities was measured at
**3–10** in earlier work. A cap-weighted test would be a bet on five megacaps
wearing a portfolio's clothing.

So: **equal-weighted, cross-sectional, and long-only.** Equal weighting is not
a preference here, it is what makes the breadth real. Long-only is not a
preference either: `forbid_shorting` enforces it at the compliance layer.

> **H1.** Within the Shariah-screened universe, ranking names by their trailing
> return and buying the weakest decile earns a gross return, over the following
> holding period, that exceeds both a resampling null and the round-trip cost.

Short-horizon cross-sectional reversal is the most documented effect that
survives in liquid US equities, which makes it the right first test: if *it*
does not appear, the apparatus is more likely wrong than the market.

## What is fixed, now

**Universe.** SPUS holdings as filed, per quarter, from
`docs/spus_universe_timeline.json`. At each rebalance, the names held *then*.
Quarterly steps; a name added and dropped inside one quarter is invisible and
that is accepted.

**Signals: two, two lookbacks each.**

| signal    | rule                                        | lookbacks |
|-----------|---------------------------------------------|-----------|
| reversal  | buy the weakest decile by trailing return   | 5d, 21d   |
| momentum  | buy the strongest decile by trailing return | 5d, 21d   |

Momentum is included as the sign-flipped twin, not as a second guess: if
reversal works and momentum does not, that asymmetry is evidence; if both
"work", something is wrong with the harness.

**Horizons: 5 trading days and 21 trading days.** Not intraday. Approach 1
aimed there and the crypto work established that a short horizon puts the move
below the cost before any signal is considered. At a daily-to-monthly horizon
the typical move is two orders of magnitude above the tick floor.

**Four configurations × two horizons = eight tests.** Bonferroni: empirical
**p < 0.00625**, not 0.05. Fixed now so it cannot be relaxed later.

**Control: a resampling null, B = 200.** Daily cross-sectional returns are
shuffled *across names within each date*, which destroys the cross-sectional
signal while preserving each day's market move and the dispersion between
names. The identical ranking and holding rule then runs on the shuffled panel.

This is the control the crypto work arrived at the hard way. A random-name
control was tried there and failed its own abort check, because it did not
share the selection mechanism. Shuffling within date does share it: whatever a
decile rule earns mechanically, it earns on the shuffle too.

**Statistic.** Mean gross return per position in bps, real minus null, with a
**date-clustered** standard error. Positions opened on one date share that
day's market and are not independent.

**Cost gate.** The edge must exceed the measured round trip from P2 — spread
plus commission plus slippage, per name, not a universe average.

**Hold-out.** Primary on 2020-05-31 → 2024-05-31 (16 quarters). Confirmation on
2024-08-31 → 2026-05-31 (9 quarters), **not examined until the primary result
is written into this file.**

## Criteria

| outcome                                                                        | conclusion |
|--------------------------------------------------------------------------------|------------|
| ≥1 configuration beats its null at p < 0.00625, exceeds cost, and repeats out of sample | **Pass.** Forward paper test, newly pre-registered. |
| Beats the null but not the cost                                                 | Real and untradeable. Recorded. Slot spent. |
| No configuration clears the bar                                                 | **Fail.** Slot spent; three remain. |
| Primary passes, hold-out does not                                               | **Fail**, reported as an in-sample artefact. Slot spent. |

**Aborts.** The run stops and the slot is **not** spent if any precondition
above fails mid-run — in particular if the priced universe falls below 80% of
the filed universe in any quarter.

## Commitments

- No signal, lookback, horizon or threshold changes after this file is
  committed. A change voids the run and requires a new registration.
- All eight results reported, including the seven that will not be the best.
- The outcome is recorded here whatever it is, following
  `APPROACH_1_PREREGISTRATION.md` and `HORIZON_PREREGISTRATION.md`.
- A pass earns a forward test and nothing else.

## Prior

Higher than crypto's, and still low. Cross-sectional reversal is documented and
the cost structure genuinely permits it, which is more than could be said of
any of the eleven crypto approaches. Against that: large-cap US equities are
the most competed venue that exists, effective breadth is 3–10 rather than 200,
and the universe is dominated by a handful of megacaps whose behaviour will
drive any equal-weighted result more than the name count suggests.

What is carried forward from the crypto closure is not a prior but a method.
Under a weaker null, four configurations there passed at +37 to +92 bps with
t between +10.8 and +13.1, and all of it was drift plus selection arithmetic.
Three safeguards were needed to kill it. All three are built into this document
before the first number is computed, rather than added once a result looks too
good.
