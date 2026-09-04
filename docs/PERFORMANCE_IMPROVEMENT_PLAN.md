# Improving win rate, profit factor and drawdown

**Status:** plan, 2026-09-04. Nothing here is implemented.
**Baseline:** 1,590 closed paper trades, one session, six symbols on Bitget,
from `explainability_ledger.jsonl` (`trade_mfe_mae` records).

## The measurement that reframes the request

|                | gross (before costs) | net (after measured round trip) |
|----------------|---------------------:|--------------------------------:|
| win rate       |            **45.7%** |                            6.2% |
| profit factor  |             **0.86** |                            0.04 |
| mean per trade |        **−0.63 bps** |                      −15.75 bps |
| median         |            +0.00 bps |                      −15.34 bps |
| average win    |            +8.20 bps |                      +10.60 bps |
| average loss   |            −8.02 bps |                      −17.51 bps |

**The strategy loses before costs are applied.** That is the finding this plan
has to be built around, because it makes the three requested metrics dependent
rather than independent.

Two supporting observations.

*The gross distribution is symmetric.* 21.4% of trades land in `−20..−5 bps`
against 20.7% in `5..20`; 3.5% below −20 against 3.5% above +20; median MFE
+2.84 bps against median MAE −2.89 bps. This is what a random entry into a
volatile instrument produces.

*Nine trades in ten never had a payable moment.* 89% of trades never reached a
point where the position was worth more than the 14 bps minimum round trip.
Not "exited badly" — the favourable excursion never got there, so no exit
policy could have made them profitable.

**What the gross figure does not say.** −0.63 bps carries a naive t of −1.75,
and every trade comes from a single day, so day-clustering cannot be estimated
and even that t is an overstatement. The honest reading is *indistinguishable
from zero*, not *reliably negative*. That distinction decides Stage 2 below.

## Why the three metrics are one metric

Win rate, profit factor and drawdown are all functions of the same per-trade
P&L distribution. Given a symmetric gross distribution and a fixed cost per
round trip, all three are determined — they are not three dials.

There are only three ways to move them:

1. **Lower the cost.** Shifts the whole distribution right. Guaranteed effect,
   bounded size.
2. **Raise the gross edge.** Changes the distribution's shape. Unbounded, and
   unproven here.
3. **Change the exit asymmetry.** Redistributes between win rate and average
   win. Only produces profit if (2) is non-zero — you cannot exit your way out
   of a random walk.

Deleveraging is deliberately not on that list. Halving position size halves
drawdown and halves returns; it improves the drawdown *number* without
improving anything about the strategy, and it is worth naming so it is not
mistaken for progress.

## Stage 1 — Cost. The only lever with a guaranteed effect

Round trip today is `spread + 2 × slippage + 2 × taker` = 14.00–25.44 bps
depending on instrument (`docs/spread_measurements.json`), ~15 bps on the
traded mix.

| change                                             | saves     |
|----------------------------------------------------|----------:|
| `EXECUTION_MODE=maker` (taker 5 bps → maker 2 bps, both sides) | 6 bps |
| restrict to the two tightest books (BTC 0.00, ETH 0.04 bps)    | ~1 bps |

**15.0 → ~4.04 bps.** Effect on the baseline:

- net mean per trade: **−15.75 → −4.67 bps**
- share of trades with a payable moment: **9.6% → 42.5%**

Both already supported: `execution_mode`, `maker_offset_bps` and `limit_ttl_s`
exist, and `ALLOWED_SYMBOLS` narrows the universe.

**What it costs.** A maker order may not fill. `limit_ttl_s = 60` cancels it,
and an unfilled maker order is a missed trade, not a free one — so fill rate
becomes the new unknown and must be measured before the saving can be claimed.
`PaperBroker._try_fill` already models this: a marketable limit is booked as a
taker fill at the touch, and only a genuinely resting order earns the maker
side. The paper figures will therefore be honest about it.

**What it does not do.** Gross is ~0, so net lands near −4 bps rather than
above zero. This stage makes the bleed roughly a quarter of what it is. It does
not make the strategy profitable, and no combination of Stage 1 changes can.

## Stage 2 — The gate: does any gross edge exist?

This decides whether Stages 3 and 4 are worth anyone's time, and it is the
stage this plan actually turns on.

The question is not "is the strategy profitable" but "does this entry rule beat
a random entry at the same timestamps, on the same instruments, before costs?"
The current answer is *unknown*: one day, t = −1.75 naive, clustering
unestimable.

What it needs, following Part 2 of `STRATEGY_RESEARCH_FRAMEWORK.md`:

- **multiple days**, enough for a day-clustered t rather than a naive one;
- **a random-entry control** at matched times and instruments, so the
  comparison is against the alternative that costs nothing to implement;
- **a pre-registered effect size and stopping rule**, derived from the power
  calculation, written before the run — not chosen once the number is visible.

If gross edge is indistinguishable from zero on that test, the honest
conclusion is that this strategy has nothing to improve, and Stages 3–4 are
parameter-fitting on noise. `docs/EQUITY_PROGRAMME_STOP_RULE.md` already
describes what to do with that outcome.

## Stage 3 — Exit asymmetry (only if Stage 2 passes)

Median MFE +2.84 bps against median MAE −2.89 bps: the excursion distribution
is symmetric, so today's stop and target placement is not leaving anything
obvious on the table.

**The trap to avoid here.** A positive expected MFE is not evidence of edge. The
running maximum of a random walk is positive by construction, so "42.5% of
trades had a moment worth more than 4 bps" is exactly what noise produces.
Capturing that moment requires knowing when the high occurs, which is the whole
problem. Any exit rule fitted to observed MFE will look excellent in-sample and
transfer nothing.

If Stage 2 establishes a real edge, the work here is placing stop and target
against the *conditional* excursion distribution given that edge — and it must
be pre-registered like anything else.

## Stage 4 — Drawdown

Observed maximum drawdown: **−1.211%**, against `dd_soft` and `dd_hard`
guardrails that never fired.

Real reductions come from higher profit factor (Stages 1–2) or from lower
correlation between concurrent positions. The second is largely unavailable
here: the crypto universe measures ~1.7 effective assets, so holding six
positions is closer to holding one and a half than to holding six. Concurrency
limits therefore change exposure, not diversification.

That leaves drawdown as a mostly *derived* quantity in this system. It is worth
tracking, and it is not worth targeting directly.

## Order of work

1. **Stage 1**, because it is configuration-only, reversible, measurable within
   a session, and moves net mean per trade from −15.75 to about −4.67 bps.
   Measure the maker fill rate as part of it, since the saving is not real
   until that is known.
2. **Stage 2**, as the gate. Everything after it is conditional on the answer.
3. **Stages 3–4 only if Stage 2 passes.**

## What this plan does not promise

It does not promise profitability, and the arithmetic says why: gross edge is
approximately zero, the best achievable round trip is around 4 bps, and a
strategy with no gross edge nets negative its costs however they are arranged.

The realistic outcome of Stage 1 is a loss roughly a quarter of its current
size. The realistic outcome of Stage 2 is a decision — including, on the
evidence so far, quite possibly the decision to stop.

That is a worse answer than the question invited, and it is the one the
measurements support.
