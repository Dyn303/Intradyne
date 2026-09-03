# Approach 1 — Intraday return predictability in US equities

**Committed before the test was written or run.** That is the only property
that makes this document worth anything. Criteria written after seeing results
are not criteria, they are a story.

This **commits** slot 1 of 4 under `docs/EQUITY_PROGRAMME_STOP_RULE.md` to this
hypothesis. Per that rule the slot is *spent* when the test runs to a verdict,
or the moment any interim result is seen — not by writing this document. If the
abort condition below is met the test never runs and the slot returns to the
budget.

---

## Why this hypothesis, and why it is shaped like this

The hypothesis with the strongest prior available — cross-sectional selection —
**cannot be tested honestly with the data this project can obtain**, so it is
not what slot one spends itself on.

`scripts/equity_liquidity.py` recorded why. Delisted names fail in two ways and
one of them is silent:

    FXEN  delisted 2015  ->  "Invalid API call"        a clean refusal
    ADVM  delisted 2026  ->  100 sessions of 4.3600    flat line, volume 0

The second is a placeholder standing where the delisting decline used to be. A
cross-sectional backtest ingesting it would score a dead company as a
zero-volatility asset rather than as a loss — survivorship bias with the sign
reversed and made worse. `scripts/equity_pit_universe.py` knows *which* names
existed on any date; their prices are not available at any price this project
is currently paying.

So the question becomes: which hypothesis can be tested honestly given that?

**A strategy that is flat at every close barely touches the gap that delisting
creates.** The catastrophic move in a delisting is a gap or a halt, and a
position that does not exist overnight does not experience it. The bias does
not vanish — the names available to test are still survivors — but it is
bounded, and A3 lets the bound be *measured* rather than asserted: the fraction
of the starting universe that delisted during the window is a number this
project can produce.

That is the entire reason this approach is intraday. It is not a preference for
intraday trading.

---

## Feasibility (A1)

Costs were re-run per price band in #28 and every band cleared at a one-day
hold. The relevant figures for an intraday hold:

| band | round trip | move / cost, 30 min |
|---|---|---|
| $5–20 | 11.20 bps | 7.4× |
| $50–100 | 3.47 bps | 13.3× |
| $200+ | 2.59 bps | 12.9× |

Cost is a floor from the one-cent tick (`100/price` bps round trip before
slippage and fees) rather than an assumption. At a 30-minute horizon the move
is 7–13× the cost, so the arithmetic is not hopeless — which is all A1 claims.

The A1 finding that shapes the universe below is that **price is the wrong
axis**: dispersion of move/cost *within* a band exceeded dispersion *between*
bands. Names are therefore selected on liquidity, not on price.

---

## Universe

Fixed **at the start of the test window** and never revised, which is what makes
the survivorship bound computable.

- Members of `docs/equity_listings.csv` (A3) listed on the start date, common
  stock only, on a primary exchange.
- Liquidity judged by `scripts/equity_liquidity.py` on a trailing window ending
  at the start date — never on today's volume.
- The **30 most liquid** qualifying names by median dollar volume.
- Every series is quality-checked before use: a run of identical closes, or
  sustained zero volume, is recorded as `no_data` and the name is dropped. This
  is the ADVM trap and it is asserted against, not hoped about.

**The survivorship bound is a required output.** The fraction of the 30 that
delisted before the window ended is reported with the result. If it exceeds
**20%**, the sample is too survivor-dependent to interpret and the result is
reported as inconclusive rather than as a finding.

30 names is chosen for a reason that is not statistical: it is what the data
quota allows at the history length the power calculation demands. Stated so the
number is not mistaken for a design choice.

---

## Signals

**Fixed at three.** Adding a fourth after seeing results invalidates the null.

1. **First half-hour return predicts the last half-hour return.** Gao, Han, Li
   & Zhou (JFE 2018). *Published specification — carries no search penalty.*
   Mechanism: infrequent institutional rebalancing and late-informed traders.
2. **Overnight gap reversal.** Enter at the open against a large overnight gap,
   exit by the close. *Published family, our parameterisation.* Mechanism:
   liquidity provision against overnight order imbalance.
3. **Intraday reversal after a large first-hour move.** *Ours.* Mechanism: the
   same liquidity provision, at a horizon the first two do not cover.

Signal 1 is the primary. It is the only one whose exact specification comes
from outside this project, so it is the only one whose result does not have to
pay for having been chosen by us.

---

## Method

Long-only, spot, **flat at every close**. No position survives a session.

Signals are evaluated through `scripts/signal_bridge.py`, so the rule tested is
the rule the engine would run — one definition, both runtimes. Each signal's
minimum buffer is measured with `minimum_buffer()` before the test, and a
signal that is not exactly reproducible at any buffer is **rejected as
non-causal** rather than traded.

**Costs:** per-band, from `equity_band_a1.py` — the one-cent tick floor plus
1 bp slippage per side plus sell-side fees. Applied on realised turnover.

**Benchmark:** random entry with matched trade count and holding geometry, on
the same bars. Every result is excess over that, never over zero. An intraday
long-only rule in a rising market earns drift, and beating zero proves nothing.

**Null:** best-of-N at the 95th percentile, built from each signal's own
geometry and trade count. **N = 3**, the signal count of this approach. Should
a later approach reuse this data, its null must count these three as well.

**Significance:** computed on the **daily** series, not on pooled trades.
Equity names correlate; A2 measured mean pairwise 0.097 intraday, giving 4.77
effective assets from 8. Treating each name-day as independent would overstate
t by roughly the square root of that ratio.

**Validation:** walk-forward, selecting on each fold and measuring on the next,
with a one-session embargo at each boundary. The reported number is what the
*procedure* earns, not what the best signal earned in hindsight.

---

## Criteria

All four must hold. Any failure is a negative result.

1. Excess over the random-entry benchmark is **> 0**
2. Excess clears the **best-of-3 null threshold**
3. Annualised Sharpe of the excess is **≥ 0.80**
4. Excess positive in a **majority of walk-forward folds** (5 folds)

### Why 0.80, and the abort condition

`t ≈ Sharpe × √years`. Two sigma therefore needs `Sharpe ≥ 2/√years`:

| history obtained | Sharpe needed for 2σ |
|---|---|
| 2 years | 1.41 |
| 4 years | 1.00 |
| **6.25 years** | **0.80** |
| 10 years | 0.63 |

**The test requires at least 6.25 years of intraday history on at least 25
names.** Below that the bar rises above 0.80 and a real edge becomes
indistinguishable from none — which is a property of the data, not a
preference.

**Abort condition:** if fewer than 6.25 years on 25 names can be assembled, the
test is **not run** and no slot is spent. That is a precondition failure, in the
same category as the missing delisted prices, and recording it is the honest
outcome. Running an underpowered test and reporting "no effect" would be
claiming an answer the data cannot give.

---

## Reporting

Committed in advance, because these are the outputs that catch a result which
is really one name or one month:

- Per-instrument contribution for every reported figure, and the **worst
  leave-one-out**. A pooled edge carried by one name is not a strategy; the
  crypto record holds a result whose t of +3.45 became 0.02 bps without a
  single instrument.
- Ranking restricted to signals clearing the trade-count floor.
- The **survivorship fraction** defined in the universe section.
- The harness falsification: random entry must earn approximately zero on this
  data before any result from it is believed.
- Each signal's measured `minimum_buffer` from the signal bridge.

---

## Commitments

- The **first** run is the result. No re-running with adjusted windows, name
  counts, or horizons until something passes.
- Three signals. That is three cells and the null accounts for three. **No cell
  is added afterwards.**
- A re-run at higher power is permitted and **spends another slot**, per the
  stop rule. "Insufficient power" is not an unlimited excuse.
- A test abandoned partway **after any result has been seen** has still spent
  this slot. Looking is what incurs the cost, not finishing.
- A negative result is written up with the same detail a positive would get.
- If a bug is found later, the fix is reported with both the before and after
  numbers, never a silent re-run.
- **This is approach 1 of 4.** Three remain after it, whatever it shows.

---

## What a positive result would and would not mean

It **would** mean: an intraday signal in liquid US large caps produced excess
over matched random entry, net of tick-floor costs, beyond what selecting among
three signals would produce by luck, over the history available.

It would **not** mean the effect is tradeable. Three things stand between a
positive here and an order:

- **The Shariah screen.** No equity has a screening record, the gate refuses
  every one of them, and the ruling on which standard applies is unresolved.
- **Settlement.** A cash account — required to avoid riba — caps intraday
  round-trips at roughly one per capital tranche per T+1 cycle, regardless of
  how many signals fire.
- **The survivorship bound.** Bounded is not zero, and the bound is measured on
  survivors.

The honest next step after a positive is **forward paper measurement**, not
deployment. `STRATEGY_EDGE_DEMONSTRATED` moves only with a decision memo
attached, per `docs/PIPELINE.md` Part 3.

---

## Prior

**Low.**

Signal 1 was published in 2018 on data ending around 2016, and post-publication
decay in equity anomalies is among the better-documented findings in the
literature. US intraday equity trading is also the most competed venue that
exists; the prior that a published intraday effect survives there, net of costs,
eight years after publication, is not high.

This project has also tested the same specification once before, in crypto,
where it returned **t = 1.79** — below even the conventional 1.96, let alone the
3.4–3.8 the multiple-testing literature asks of a discovery. That was a
different market, so it does not settle this one, but it does not encourage.

What running it buys is the same thing the final crypto test bought: **it
converts a caveat into an answer.** The intraday direction is either worth the
remaining three slots or it is not, and one properly-powered test on the
best-supported signal in its own native market is the cheapest way to find out.

---

## Outcome: precondition failure — the slot was not spent

**Recorded 2026-09-03, before any signal was computed.** No return, no
t-statistic and no ranking was produced. The abort condition fired on data
reachability, which is a statement about the provider and not about the
hypothesis.

### What was measured

A seeded draw of 120 names from the 5,590 qualifying common stocks
(`docs/approach1_sample.json`, seed 20191101), fetched at 30-minute bars over
the ranking window 2019-11 to 2019-12:

| | count | reachable |
|---|---|---|
| live today | 83 | **59 (71%)** |
| delisted since | 37 | **2 (5%)** |
| **total** | **120** | **61 (51%)** |

**Lost to delisting: 29.2%**, against the 20% ceiling this document set.

### Why the panel could not be made honest

Half a random draw from the universe is unfetchable, and the missing half is
not a random half. Three causes, none of them noise:

- **Delisting.** 35 of 37 dead names return nothing, or the frozen-price
  placeholder `equity_liquidity.py` records. These are, by construction, the
  names that did worst.
- **Rename and restructuring.** `FBIN` was FBHS until 2022, `CXT` is a 2023
  Crane spin-off, `PLUR` was Pluristem, `VIVS` was Organovo, `SGLY` was
  Sino-Global. `equity_listings.csv` holds one symbol per listing — today's —
  so a company that changed ticker is unreachable under the name A3 knows it
  by. Renames cluster around mergers and restructurings, so this too removes
  names where something happened.
- **Misclassification.** `SCHW-P-D`, `TRTN-P-C`, `TY-P` and `RILYP` are
  preferred lines; `PDX` is a closed-end fund; `WX` and `DOYU` are ADRs. All
  carry `assetType == "Stock"`. The name-based filter catches warrants and
  units because their *names* say so; a preferred line whose name reads
  "Charles Schwab Corp" defeats it, and the ticker suffix is the only
  signal.

A test on the surviving 51% would measure intraday behaviour among companies
that stayed listed, kept their ticker, and were classified correctly. That is a
different question from the one this document asks, and the difference runs in
the flattering direction.

### One bug found, and it moved the number

The first run reported 43 of 83 live names reachable (52%). That was wrong.
`fetch_twelvedata` returned `None` on any non-200 including **429**, and the
caller printed it as "no data" — a throttle recorded as an absence, at 7.9
requests a minute against an 8/minute ceiling. Rate limits are now retried and
the sleep floor raised; the corrected figure is 59 of 83 (71%). The before and
after are both stated here rather than the first number being quietly replaced.

### What this does and does not settle

It does **not** say anything about intraday predictability in US equities. The
hypothesis is untested and the three signals were never computed.

It does say that **this project cannot currently assemble a survivorship-honest
equity panel at any horizon**, which is a stronger and more useful finding than
one approach's verdict. It blocks the cross-sectional approach for the same
reason, and more severely.

### What would unblock it

A survivorship-free intraday source — premium Alpha Vantage, Polygon, Databento
or similar — with point-in-time symbology so a renamed company stays reachable.
Until then the constraint is the data, not the ideas, and spending slots against
it would spend the budget on measuring a provider.

**Slot 1 returns to the budget. Four approaches remain.**
