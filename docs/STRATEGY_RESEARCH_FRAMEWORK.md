# Strategy research framework

A standard for discovering, validating and selecting trading strategies, and
for knowing when to stop looking.

This is an adaptation of a general framework document to what this project
actually measured. The source proposed generating ~100 candidates, filtering to
~50, ~20, ~10, and selecting a complementary Top 5. That is close to what this
repository already ran -- ten times -- and the amendments below are the
difference between the two, each one attached to a specific result rather than
to a principle.

---

## Part 0 -- What this authorises

**Crypto is closed. This document does not reopen it.**

If you have arrived here intending to re-run one of the searches in
`MIGRATION.md`, that is the outcome this document exists to prevent, and
`docs/FULLSTACK_PLAN.md` names it as this project's most likely failure mode.
Ten approaches were tested and all were negative, for reasons that are
structural rather than about effort: crypto is roughly 1.7 independent assets
at intraday horizons, so statistical power cannot be bought by adding
instruments; and the documented alpha sits in coins whose costs exceed their
moves. The best entry signal measured here is worth about **0.5 bps** against a
round trip of **4-14 bps**. That gap is an order of magnitude, not a tuning
problem.

The project's own summary, written when the fifty-signal screen finished:

> **The honest top five is an empty list.** More signals will not fix this --
> fifty across eight families produced a tighter cluster than chance.
> (`MIGRATION.md:665`)

**What this document is for** is a research programme in a *different market*
-- one with enough independent assets for the portfolio reasoning in Part 4 to
mean anything. It is a rulebook such a programme must satisfy before it runs,
not a plan to run one. Instantiating it requires a pre-registration committed
to git first; see `docs/PREREGISTRATION_TEMPLATE.md`.

**No document approves trading.** The terminal gate is
`STRATEGY_EDGE_DEMONSTRATED` at `src/intradyne/core/config.py:281`, which
refuses to start the trading loop. Flipping it is a claim that an edge has been
demonstrated and must ship with the measurement that demonstrates it.

---

## Part 1 -- What the source framework gets right

Kept without argument, because they are correct and mostly already the
architecture here:

- **AI generates hypotheses, not truth.** Deterministic testing decides.
- **Highest backtest return is not the best strategy**, and robustness across
  environments beats optimisation within one.
- **Every result must be reproducible** -- strategy version, parameters,
  dataset version, fees, slippage, seed.
- **Look-ahead prevention** as a rejection condition rather than a review note.
- **Out-of-sample and walk-forward validation**, with the final holdout treated
  as a locked vault that a single confirmation run may open once.
- **Parameter stability**: a strategy that works at RSI 53 and fails at 52 and
  54 is a fitted artifact.
- **Stress testing** across fees, slippage, execution delay and dropped trades.
- **Explicit lifecycle states** for every strategy.
- **Research and production kept separate**, with experimental code unable to
  reach live trading. This repo already enforces it:
  `api/routes/research_record.py` is read-only by construction and a test
  asserts the dashboard never reaches a compute endpoint.

Three further points in the source are **gaps this repository genuinely has**,
and they are its most useful contribution:

- **A strategy/results database.** The research record here is a hardcoded
  registry over loose JSON files that share no schema, written by separate
  scripts over months. The renderer detects shape and falls back to formatted
  JSON.
- **An experiment ID** carrying dataset version, code version and parameter
  seed. Nothing here stamps a result with the commit that produced it.
- **Durable run history.** `artifacts/` is gitignored, so the JSON behind the
  research record does not survive a fresh clone.

These are recorded as known gaps in Part 6 rather than solved here.

---

## Part 2 -- The amendments

Four stages. Each rule replaces something in the source framework, and each is
attached to a measured result rather than an argument.

### Stage A -- Before a single candidate is generated

**A1. Gate on feasibility before generating anything.**

The source treats cost as a robustness check applied after ranking (§7, §17).
Invert it. Compare the round-trip cost against the plausible edge scale in the
target market *first*, and if the ratio is hopeless the programme stops before
candidate one.

This is the rule that would have saved this project the most time. The measured
intraday edge was real -- `breakout_300s` at +0.49 bps over random entry at 4.3
sigma across 6,391 trades -- and irrelevant:

> Round-trip taker cost is **28-35x the excess** (+0.49 vs 14 bps). All-maker
> fills at 4 bps are still **8-10x** the excess. Breaking even requires
> round-trip costs below **~0.5 bps**. (`MIGRATION.md:761-764`)

`src/intradyne/backtester/costs.py` already computes what this gate needs:
`round_trip_cost_pct()`, `breakeven_win_rate()` and `expectancy_pct()`.

**A2. Compute the minimum detectable effect before choosing criteria.**

The source notes that a profit factor of 5.0 on 12 trades is worthless (§10)
but never asks what effect size the available data *can* resolve. Derive the
thresholds from the history rather than picking them:

> Strategy-level detection follows `t ~= Sharpe * sqrt(years)`. The universe is
> complete from 2020-10, so roughly 5.9 years are available, and 2 sigma
> therefore needs Sharpe >= 0.82. This is a property of the available history,
> not a preference: below that, a real edge and no edge are indistinguishable
> with the data that exists, and more frequent rebalancing does not help --
> observation count is not calendar time.
> (`docs/CROSS_SECTIONAL_PREREGISTRATION.md:79-86`)

If the minimum detectable effect exceeds the effect you are looking for, the
test cannot answer the question and must not be run. This project ran a
four-day tick screen whose long horizons had 27-42 trades against a per-trade
standard deviation of 56 bps, and concluded "no edge" when the honest answer
was "no power". Detecting a 0.5 bps effect against 9 bps noise needs on the
order of 1,300 trades.

**A3. Build the universe point-in-time, and keep the dead.**

The source says "asset universe" (§6) with no construction rules. Two biases
enter otherwise, and both flatter:

- **Survivorship.** Taking today's tradeable names and running them backwards
  selects assets *because they survived*. Of the names this project tracked,
  583 were listed at some point and roughly 180-316 no longer trade. Delisting
  dates were validated against real events -- SRM at the FTX collapse, MATIC at
  the POL migration, OCEAN and AGIX folded into FET.
- **Liquidity look-ahead.** Judge tradeability from a window ending at the
  rebalance date, never from today's volume. Using current liquidity to decide
  what was tradeable in 2022 leaks the future as surely as using current
  listings.

A name that later dies stays in every snapshot it belonged to, and its loss is
taken at its last traded price. `scripts/point_in_time_universe.py` implements
this and is market-agnostic in structure.

**A4. Pre-register, and commit it before the test is written.**

The source has decision gates (§36) but evaluates them after seeing data, which
is not the same thing:

> **Committed before the test was run.** That is the only property that makes
> this document worth anything. Criteria written after seeing results are not
> criteria, they are a story.
>
> The reason for the formality is specific rather than ceremonial. Over the
> course of this work, four separate apparently-profitable results dissolved
> under inspection [...] A screen of fifty signals then produced a confident
> "top 5" that was pure selection bias. The common thread is that a plausible
> number arrived and nobody had written down in advance what would have counted
> as failure. (`docs/CROSS_SECTIONAL_PREREGISTRATION.md:3-14`)

The value is not hypothetical. In the final crypto test one cell passed all
four criteria with a Sharpe of 0.94 -- and the pre-registration had ruled it
out in advance, because it was the secondary universe and the document said
beforehand that the secondary cannot rescue a failed primary. Without that
sentence, written before the run, it would have been written up as a
defensive-factor discovery with a plausible story attached.

### Stage B -- How candidates are measured

**B1. Share the exit mechanics; candidates differ only in when they enter.**

The source lets every candidate carry its own entry *and* exit logic (§6),
which means a candidate can win by accidentally receiving better exit geometry
rather than better timing. Compute the forward outcome of entering at every bar
once, and make a strategy a boolean mask over it:

> `forward_outcomes` computes the net result of entering at every bar once; a
> strategy is only a boolean mask over that. No strategy can win by accidentally
> getting a different exit rule, and when a bar spans both target and stop it is
> scored as the stop, since bar data cannot say which came first.
> (`MIGRATION.md:594-599`)

Scoring the stop when a bar spans both is the pessimistic choice, and it is the
right one: bar data cannot say which came first.

**B2. Measure the realised holding period, never the nominal one.**

A declared timeframe is not a measured one. This project ran an entire intraday
search that was silently measuring scalps:

> `hold` is only a *maximum*, so a 10bps stop exits in seconds regardless of
> what the holding parameter says. The evidence was in the output and went
> unread -- an 11.7% win rate on a "240 minute" strategy, and 21,310 trades over
> 20 months, about 35 a day. (`MIGRATION.md:929-935`)

Print the median realised hold and the trades per day next to the configured
parameters, so the two cannot be read apart.

**B3. Gate on cost first, then rank only among survivors.**

This replaces the source's weighted 100-point score (§9). See Part 3 for why
the weighting itself is the problem. The ordering:

```
Tier 0   enough non-overlapping trades to measure anything
Tier 1   gross edge per trade exceeds the round trip
Tier 2   net edge beats the best-of-N null
Tier 3   net edge still positive on held-out data
Tier 4   still positive at taker cost, not just all-maker
```

> Tier 1 sits first because nothing downstream can rescue a strategy whose
> gross edge does not clear its own costs. Ranking on win rate or Sharpe before
> that question is settled is how a search produces a confident, worthless top
> five. (`MIGRATION.md:890-892`)

The tiers are fixed before the run, in the commit that introduces them.

**B4. Measure excess over a matched benchmark, never over zero.**

The source ranks absolute return, profit factor and drawdown (§9-§12). A
long-only rule in a rising market earns whatever the market did, and that
dwarfs any edge:

> Over months, a long-only rule at an hour-plus horizon earns whatever the asset
> did, and that dwarfs costs. Beating zero proves nothing. Every result below is
> therefore *excess over random entry* on the same bars -- the unconditional
> mean, carrying identical drift. (`MIGRATION.md:686-692`)

For a time-series strategy the benchmark is random entry with matched trade
count and geometry. For a cross-sectional one it is the equal-weighted
universe, which carries identical drift *and* identical survivorship
composition, so the excess is not flattered by either.

### Stage C -- How results are judged

**C1. Use a best-of-N null, built from each strategy's own geometry.**

This is the largest single gap in the source framework. §9 ranks 100 candidates
on absolute score and takes the top five; §18 then repeats the procedure for
generations two and three. Testing N strategies and reporting the best always
produces winners, because the maximum of N noisy estimates is biased upward.

The null is the distribution of *best-of-N under no edge*: draw random entry
rules with matched trade counts, take the best of each draw, and use the 95th
percentile as the bar. **N counts every candidate that has touched the data**,
across every generation -- not the number in the current batch.

Critically, the null must be built per strategy rather than once:

> The null was being drawn from whichever geometry happened to be cached first
> and applied to every strategy, which made the bar arbitrary in exactly the
> tier that rejects everything. [...] The best-looking strategy on each
> instrument has the *highest* null, because a few hundred trades at tp300/sl150
> is a small, high-variance sample and best-of-N selection produces +23 to
> +29bps there by luck alone. (`MIGRATION.md:956-975`)

| strategy | trades | gross | its own null |
|---|---|---|---|
| ema5x30+ofi30 (ETH) | 206 | +12.00 | **+28.86** |
| break60 (BTC) | 319 | +12.11 | **+23.48** |

A single flat threshold flatters exactly the strategies least able to support
the weight. Implementation: `null_threshold()` at `scripts/strategy_search.py:351`.

**C2. Cluster the significance test by time, and compute effective breadth.**

The source has no significance test at all. Two rules:

- **Never treat correlated instruments as independent observations.** Mean
  pairwise correlation of 0.563 put the effective number of independent
  instruments at **1.7 of 20**, making the pooled sample worth about 9% of its
  trade count. A strategy measured at t = 3.91 across 25,946 trades was nearer
  **t = 1.2**.
- **Average within a period and test the period series.** Mechanically: group
  by day, average, then `t = mean / (std / sqrt(n))` on the daily series.
  `scripts/multi_instrument_search.py:75-95`.

Compute effective breadth before claiming sample size. Category labels are not
evidence of independence -- in crypto they described what a token claimed to do,
not what moved its price, and only PAXG (a claim on physical gold) decorrelated.

**C3. Print the per-instrument contribution, and the leave-one-out.**

The source correlates strategies against each other (§21) but never asks where
a strategy's return came from. A pooled average can be carried entirely by one
name:

| strategy | that name's share of the edge | from % of trades | edge without it |
|---|---|---|---|
| low30+ofi10 | 75% | 18% | +15.78 -> **+4.76** |
| low60+ofineg30 | **100%** | 9% | +5.35 -> **+0.02** |

> `low60+ofineg30` [...] has the strongest out-of-sample t-statistic of the
> three at +3.45, and without PEPE its edge is **0.02bps**. It is not a
> strategy; it is a long position in one memecoin during an extraordinary run,
> wearing an entry rule as a disguise. (`MIGRATION.md:1210-1213`)

"Positive on most instruments" is not robustness. Report the per-instrument
table and the worst leave-one-out result alongside every pooled figure.

**C4. Rank only among candidates that cleared the sample-size floor.**

> the top-5 table originally ranked over every strategy rather than over Tier 0
> passers, which put a one-trade, 100%-win artifact (+63.47bps, PROMUSDT, n=1)
> at the top. That is exactly the impression the filter exists to prevent.
> (`MIGRATION.md:1078-1082`)

**C5. Walk forward by selecting on one fold and trading the next.**

The source describes walk-forward (§14) but then reports the winner's test
score, which still flatters because the winner was chosen by looking:

> Selection happens on each fold and is measured on the next. The reported
> number is what the *procedure* earns, not what the best signal earned in
> hindsight. (`docs/CROSS_SECTIONAL_PREREGISTRATION.md:64-66`)

Add a purge and embargo around each boundary. Where labels are built from
forward returns over a horizon, a contiguous cut leaks: the last training
observations overlap the first test ones. Current tooling here uses contiguous
blocks with no embargo, which is a known weakness rather than a solved problem.

### Stage D -- Trusting the answer

**D1. Falsify the harness before believing it.**

The source has no harness validation anywhere. A negative result is worth
nothing if the simulation was rigged to produce it, and a positive one is worth
less:

> selecting names at random earns **-0.09%** excess per period against an
> expected zero, across 300 trials. The machinery is unbiased, and
> `test_random_selection_earns_no_excess` pins it. (`MIGRATION.md:844-847`)

Random input must earn zero, and the check belongs in the test suite.

**D2. Held-out data is not one filter among many.**

The source lists out-of-sample testing as one stage of several (§13). In this
project's record it is the only gate that ever caught a false positive:

> Every in-sample guard -- the cost gate, the clustered t-statistic, the
> per-strategy null, consistency across ten instruments -- passed. None of them
> detected that the effect would not survive the next eleven months. Only
> running it on data the selection never touched did that.
> (`MIGRATION.md:1166-1170`)

The strategy in question cleared a day-clustered t of 4.58, beat its own null,
and was positive on 10 of 10 instruments.

**D3. Declare a programme-level stop rule, and a terminal state.**

The source's evolution loop (§18) ends with the word "Continue", and its
lifecycle (§25) has no state for a research programme that found nothing. A
framework with no stop rule cannot produce a closed question -- it can only
produce an indefinite search, and an indefinite search against a fixed dataset
eventually finds something by luck.

Every programme declares in advance how many approaches it gets and what result
ends it. The sentence that closed crypto was written before the test that
closed it:

> **This is the last crypto test.** Whatever it shows, the search stops.
> (`docs/CROSS_SECTIONAL_V2_PREREGISTRATION.md`)

Add `PROGRAMME_CLOSED` to the lifecycle in Part 5.

**D4. Report a negative with the detail a positive would get.**

And never re-run silently after finding a bug. If a defect is found, report the
fix with both the before and after numbers. A better-powered version of a
flawed test is a *new* pre-registered test with its own criteria fixed in
advance, not a correction to the old one.

---

## Part 3 -- Why the weighted score is replaced

The source framework scores candidates on a weighted 100-point scale (§9):
drawdown 20, profit factor 20, out-of-sample 15, consistency 15, Sharpe 10, fee
robustness 10, parameter stability 5, trade count 5.

This project tested that exact shape -- a component hierarchy weighted
20/20/15/20/10/10/5 -- and rejected the weighting before running it, on two
grounds:

> **Gates rather than weights.** Seven weights are seven fitted parameters, and
> this project has already produced a strategy that reached a day-clustered t of
> 4.58, beat its own null, was positive on 10 of 10 instruments and then lost
> money over the following eleven months. As AND-gates the hierarchy has no free
> parameters, so any result is not a tuning artifact. (`MIGRATION.md:1240-1244`)

And the weights encoded an independence the data denied. Seven components
carried about **3.2 independent dimensions**, so a scheme placing 85% of its
weight on the directional cluster "feels far more confirmed than it is". (Worth
noting the contrast: 3.2 of 7 is far better than instruments manage at 1.7 of
20 -- combining indicators genuinely does add information in a way that adding
correlated assets does not.)

There is a third reason specific to scoring. A weighted score lets a good
drawdown number compensate for a gross edge below costs. That is not a tradeoff
that exists: a strategy whose edge does not clear its own costs loses money
regardless of how smooth the losing is. Hard gates, then ranking among
survivors, is the only ordering that respects that.

---

## Part 4 -- What changes in a different market

The source framework assumes 24/7 spot crypto without saying so. Moving to
equities changes more than the data source.

**Breadth is the reason to move, but it is finite.** The source's §20-§22 --
diversification across strategy families, a correlation matrix, portfolio-level
testing -- are sound in principle and were fitting noise here. At 1.7 effective
assets, a five-strategy correlation matrix is mostly estimation error.

An earlier draft of this section claimed equities offer "hundreds of genuinely
independent names". **That is wrong, and measurement corrects it.** Effective
breadth saturates: `N_eff = N / (1 + (N-1) * rho_bar)` tends to `1/rho_bar` as
names are added, so the ceiling is set by correlation, not by universe size.

| rho_bar | N=13 | N=50 | N=200 | ceiling |
|---|---|---|---|---|
| 0.097 (measured, 8 diverse large caps, 30-min) | 5.4 | 7.4 | 8.2 | **10.3** |
| 0.30 (broad universe, more same-sector pairs) | 2.8 | 3.2 | 3.3 | **3.3** |
| 0.563 (crypto, hourly) | 1.7 | 1.8 | 1.8 | **1.78** |

So equities buy roughly **3 to 10 effective assets, not hundreds** -- real, and
2-6x crypto, but a far more modest claim. The same table sharpens the crypto
result: at a ceiling of 1.78, twenty coins already delivered 1.71. **Crypto was
saturated.** Adding instruments was not merely inefficient, it was
arithmetically incapable of helping, which is why widening the universe was
never going to answer a power problem.

Measured by `scripts/equity_breadth.py`: 8 names, 194 sessions, day-clustered
standard error 0.009 on rho_bar. Two caveats travel with the number -- it is a
deliberately diverse handful rather than a real universe, and it covers one
regime with no crisis in it, while equity correlations rise sharply in
drawdowns, which is when breadth is worth most.

**Costs amortise over a longer horizon -- and clear at short ones too.** The
breakeven arithmetic that killed the crypto work -- a 14 bps round trip against
a 3.5 bps expected move at the two-minute horizon, so cost was four times the
entire move -- reverses in equities. `scripts/equity_feasibility.py` measures a
round trip near 4.3 bps against two-minute moves of 5.9 to 13.4 bps across the
same 8 names: **1.4x to 3.1x in favour**, where crypto was 4x against.
Breakeven holding period is seconds, against the ~31 minutes crypto needed.

That means the source's "recommended initial execution timeframe: 5-minute"
default is not automatically disqualifying here, as it was for crypto. It does
not make intraday *advisable*: US intraday equity is the most competed venue in
finance, and a cash account -- required to avoid riba, since margin is not
available -- caps round trips at roughly one per capital tranche per T+1
settlement cycle. Cost stops being the binding constraint; competition and
settlement replace it.

**Shariah screening becomes point-in-time, and interacts with A3.** The crypto
model in `src/intradyne/risk/shariah.py` is an allow-list plus tag exclusion,
which is static. Equity screening is financial-ratio based -- debt and
interest-bearing assets relative to market capitalisation, non-compliant
revenue share -- and those ratios move every quarter. Screening a historical
backtest on today's ratios is a look-ahead leak the allow-list model never had.
The compliance screen must be rebuilt at each rebalance date from data
available at that date, and income purification is a cost line the backtest has
to carry.

**Mechanics the source never mentions**, all hidden by a 24/7 spot assumption:
corporate actions and adjusted prices; restatement lag, so fundamentals are
known only after the filing date rather than the period end; market hours,
opening and closing auctions, and halts; settlement. Each is a look-ahead
vector, and A3's discipline extends to all of them.

**What does not change:** long-only and spot-only remain structural, so nothing
in this framework may express a short, leverage, or a position that survives
its stop.

---

## Part 5 -- Decision gates and lifecycle

A strategy becomes a finalist only after passing, in this order: the
feasibility gate (A1), the power check (A2), a cost-first tier filter (B3), its
own best-of-N null (C1), a time-clustered significance test (C2), the
per-instrument breakdown (C3), walk-forward validation (C5), held-out data
(D2), stress testing across fees, slippage, delay and dropped trades, and
finally forward paper measurement.

Lifecycle:

```
GENERATED -> BACKTESTED -> SHORTLISTED -> ROBUSTNESS_TESTED -> OOS_VALIDATED
    -> WALK_FORWARD_VALIDATED -> STRESS_TESTED -> PAPER_TRADING -> APPROVED -> LIVE

degradation:  LIVE -> DEGRADED -> REVIEW -> PAUSED / RETIRED
programme:    ... -> PROGRAMME_CLOSED
```

`PROGRAMME_CLOSED` is terminal and applies to the research programme, not to a
strategy. It is what the crypto work reached, and reopening it requires a new
pre-registration that states what has changed about the world -- not a new idea.

Two gates already exist in code and are the real ones:

- **`assess()` in `src/intradyne/backtester/costs.py:98`** returns a verdict
  ladder: `impossible`, `insufficient_data` (under 100 trades),
  `below_breakeven`, `marginal`, `clears_with_margin`. A run must reach
  `clears_with_margin`. `marginal` is not a pass -- breakeven plus a rounding
  error is not a strategy.
- **`assert_strategy_edge_gate()` in `src/intradyne/core/config.py:287`**
  refuses to start the trading loop while `STRATEGY_EDGE_DEMONSTRATED` is
  False. It is overridable only by an explicit, noisy `ACKNOWLEDGE_NO_EDGE`,
  because paper trading is the legitimate path to validating a replacement. The
  reasoning is worth keeping in mind for anything this framework produces: *"A
  result that lives only in a markdown file is one `git pull` away from being
  forgotten, so it is enforced here instead."*

---

## Part 6 -- What transfers, and what does not

**Market-agnostic; reuse directly.**

| capability | where |
|---|---|
| round-trip cost, breakeven win rate, expectancy, verdict ladder | `src/intradyne/backtester/costs.py` |
| best-of-N null threshold (95th percentile) | `scripts/strategy_search.py:351` |
| day-clustered t-statistics and `per_symbol` breakdown | `scripts/multi_instrument_search.py:75-98`, `scripts/multi_common.py` |
| shared exit mechanics (`forward_outcomes`) | `scripts/strategy_search.py:111` |
| tiered filter discipline | `scripts/random_strategy_search.py:16-20` |
| walk-forward (select on fold i, trade fold i+1) | `scripts/strategy_months.py`, `src/intradyne/engine/cv_eval.py:13` |
| explicit in-sample / out-of-sample holdout | `scripts/sweep_edge.py --holdout` |
| point-in-time universe construction | `scripts/point_in_time_universe.py` |

**Crypto-bound; needs replacing for another market.**
`scripts/fetch_klines_archive.py` and `scripts/fetch_ohlc.py` (Binance archive
and CCXT); `scripts/build_universe.py` (Binance + CoinGecko, token-tag
screening); the venue adapter in `src/intradyne/adapters/`; and the allow-list
plus tag model in `src/intradyne/risk/shariah.py`, per Part 4.

**Known gaps, carried forward rather than solved here.**

- **No results database or run lineage.** The research record is a fixed
  registry over loose JSON; there are no run IDs, timestamps, parameter
  provenance, or diffing between runs, and `artifacts/` is gitignored. This is
  the source framework's §26-§28, and it is a fair criticism.
- **No bridge between the research path and the engine path.** Research
  strategies are vectorised numpy masks over `Bars`; engine strategies are
  classes implementing `on_tick`. A signal found in research must be
  re-implemented before it can be traded, and nothing checks the two agree.
- **`routes/research.py` computes on synthetic prices**, not real data, so
  those endpoints are decorative for research purposes.
- **No purge or embargo** in the walk-forward splits (C5).
- **No deflated Sharpe, White's Reality Check or Hansen's SPA.** The best-of-N
  null approximates them by simulation, which is defensible but not the same
  thing.
- **No route to permit an approved equity yet.** The gate now refuses any
  equity without a current screen record, but nothing loads approved records
  into it. `config.py:198` appends `/USDT` to any bare ticker in
  `ALLOWED_SYMBOLS`, and `api/routes/data.py:33` rejects slash-free symbols
  with `400 invalid_symbol` before the allow-list is consulted. Both need doing
  before an equity can trade -- and neither is urgent while there is no ruling
  to load, which is why the screener produces a worksheet rather than a live
  allow-list.
- **No point-in-time equity universe.** A3 still needs one, built from
  `LISTING_STATUS` with delisted names retained --
  `scripts/point_in_time_universe.py:67` is the direct analogue, and takes the
  dead names from the archive precisely because the live listing knows only
  about survivors.
- **A1's cost model is a large-cap number.** 4.3 bps assumes penny-wide
  spreads on liquid names. One cent is 20 bps on a $5 stock, so the gate must
  be re-run per price band before trading below roughly $20.

**The A1 and A2 gates, and the data behind them, are implemented.**

| gate | script | output |
|---|---|---|
| A1 feasibility -- does the move clear the round trip? | `scripts/equity_feasibility.py` | `artifacts/equity_feasibility.json` |
| A2 breadth -- how many independent bets exist? | `scripts/equity_breadth.py` | `artifacts/equity_breadth.json` |
| bars for both | `scripts/fetch_equity_bars.py` | `data/equities/{SYMBOL}_{interval}.csv` |
| Shariah worksheet | `scripts/screen_equities.py` | `docs/EQUITY_SCREENING.md`, `docs/equity_screen.json` |

Both gates exit non-zero on failure, so they can be wired into a check rather
than read by eye. Both carry their own falsification: the breadth script
verifies that independent input returns `N_eff ~ N` and a synthetic common
factor at rho returns `~1/rho` before reporting anything (D1).

The screener is a **worksheet, not a decision** -- the posture
`scripts/build_universe.py` takes for crypto. It reports what each ticker is
and which categories raise a question; which categories pass is a scholarly
ruling, and the thresholds and excluded activities are configuration so that
every record names the standard it was screened against.

Its first job is filtering instrument type, because the movers lists are
mostly derivatives: in the response it was written against, **14 of 20 top
gainers and 16 of 20 top losers were warrants or rights**, and most-active
carried leveraged and inverse products. Instrument type is decided from the
listing *name* rather than the ticker suffix -- `assetType` reads "Stock" even
for warrants, and a suffix rule would discard `LOW`, `BKR` and `AMCR`.

It is also a **live screen, never a research universe**. Names selected by what
already moved today cannot define a backtest universe without selecting on the
outcome, which is what A3 exists to prevent.

---

## Summary

The framework this adapts is sound engineering. What it lacked was defence
against the specific way strategy research fails: not bad ideas, but good
procedure applied without a null, without a benchmark, without a significance
test, and without anything written down beforehand that would have counted as
failure.

Generate many, eliminate weak, test on unseen data, stress the survivors, and
select a small complementary portfolio -- but measure every step against what
random selection would have produced, and decide in advance when to stop.
