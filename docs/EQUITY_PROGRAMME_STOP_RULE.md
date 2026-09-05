# Equities programme: stop rule

**Committed before the first candidate exists.** That is the only property that
makes this document worth anything, and it is the same property that makes the
two cross-sectional pre-registrations worth reading.

This is amendment D3 of `docs/STRATEGY_RESEARCH_FRAMEWORK.md`, instantiated.
The framework requires a programme to declare in advance how many approaches it
gets and what result ends it, because a search with no terminal state cannot
produce a closed question -- it can only produce an indefinite search, and an
indefinite search against a fixed dataset eventually finds something by luck.

Crypto got ten approaches. Not because ten was chosen, but because **nobody said
in advance how many it got**, and each additional one felt free at the moment it
was proposed.

---

## Crypto reopened once, under a bound (2026-09-05)

This document records that crypto got ten approaches because nobody said stop.
It has since been reopened once, and the condition is recorded here so the
reopening is visible from the place that closed it.

The justification was not a new signal but a measurement: at the 2-minute
holding period every prior approach used, the typical price move is 2.7 bps
against a 14 bps round trip. A perfect predictor loses there, so those ten
results are evidence about the horizon rather than about signals.

The bound: **one** approach, fully pre-specified in
`docs/HORIZON_PREREGISTRATION.md` before any signal touched the data. If it
fails, crypto closes and does not reopen on a further reframing — the next
reopening requires a new instrument class, not a new angle on this one.

## The budget: four approaches

The equities programme gets **four**.

Four is not a compromise between thoroughness and impatience. It is a
statistical choice. Every approach that touches the data raises the best-of-N
null threshold for every other approach, because N in amendment C1 counts every
candidate that has ever been scored against this data -- across approaches, not
within one. A fifth approach does not merely cost time; it raises the bar the
first four had to clear, retroactively. A small budget is *cheaper in evidence*,
not only in effort.

Four is also roughly the number of genuinely distinct hypotheses available:
cross-sectional selection, intraday time-series, a literature replication, and
one learned or combination method. Beyond that the ideas start being variants of
each other, which the next section refuses to let you count separately.

---

## The ledger

One place of record, so the budget cannot be lost track of. A slot is *spent*
when its test runs to a verdict or any interim result is seen — not when its
pre-registration is written.

| slot | hypothesis | pre-registration | state |
|---|---|---|---|
| 1 | Intraday return predictability, flat at every close | `docs/APPROACH_1_PREREGISTRATION.md` | **not spent** — precondition failure on data reachability |
| 2 | — | — | unspent |
| 3 | — | — | unspent |
| 4 | — | — | unspent |

Slot 1 was committed and then returned unspent: only 51% of a random draw
from the qualifying universe proved fetchable, 29% of it lost to delisting
against a 20% ceiling, so no signal was ever computed. The budget still
stands at four.

Slot 1 does not test the strongest hypothesis available. Cross-sectional
selection has the better prior and is what A2's breadth finding was built for,
but it cannot be tested honestly: delisted names return either a hard error or
a frozen price at zero volume, so a point-in-time backtest would score dead
companies as zero-volatility assets. A strategy flat at every close barely
touches that gap, which is why slot 1 is intraday. That is a data constraint
being respected, not a preference.

---

## What counts as an approach

**An approach is a hypothesis about why an effect should exist -- not an
implementation of one.**

This is the clause that matters, because it is the one crypto did not have. Of
those ten approaches, at least four were the same hypothesis re-entered through
a different door:

| what it was called | what it actually was |
|---|---|
| cross-sectional v2 | v1, with more power |
| pooled multi-instrument | the single-instrument search, with more data |
| the diverse set | the same pooled search, different names |
| the mid-cap band | the same pooled search, different slice |

Each was justified at the time, and each justification was reasonable in
isolation. Together they turned a four-approach programme into a ten-approach
one without anyone deciding to.

So, explicitly: **changing the universe, the slice, the parameter ranges, the
rebalance frequency, the instrument count, or the statistical power of a failed
approach does not create a new approach.** It is the same approach, and it is
already spent.

A new approach requires a different *reason* an effect should exist.

---

## What consumes a slot

- Any pre-registered test that runs to a verdict, including a negative one.
- A test **abandoned partway after any result has been seen.** Looking is what
  spends the slot, not finishing. A search stopped early because the interim
  numbers were discouraging has still consumed its multiple-comparisons cost,
  and pretending otherwise is how a programme quietly runs twice its budget.
- A re-run of a failed approach at higher power. This is permitted and it costs
  a slot. Making it free would turn "insufficient power" into an unlimited
  excuse, which is precisely the shape of the v1 → v2 step in crypto.

## What does not consume a slot

- Infrastructure: fetchers, universe construction, cost measurement, screening.
- Runs of the A1, A2 and A3 gates themselves. They are preconditions on whether
  a question can be asked, not attempts to answer it.
- A bug fix, provided it is reported with both the before and after numbers as
  amendment D4 requires. A silent re-run after a discovered defect consumes a
  slot *and* invalidates the result.

---

## Early stop

**If A1 fails on every tradeable price band, the programme closes immediately** —
before any candidate is generated.

A1 compares the round-trip cost against the plausible edge scale. If no band
clears it, no strategy in that band can, and generating candidates would be
measuring something already known to be unprofitable. This is the gate that
would have saved the crypto work months: the edge there was real at 0.49 bps and
irrelevant against a 4–14 bps round trip, and that arithmetic was available
before any of the ten approaches ran.

The A1 figure validated in PR #22 is **4.3 bps for liquid large caps**. It does
not transfer downward: one cent is 20 bps on a $5 stock and 5 bps on a $20 one,
so A1 must be re-run per band, and this stop triggers only if *every* band fails.

---

## Standing preconditions

These block progress rather than closing the programme. An approach may not run
until all three hold:

1. **A Shariah ruling on which screening standard applies.** AAOIFI, DJIM, S&P
   and MSCI differ in thresholds and denominator.
   `scripts/screen_equities.py` produces a worksheet and states that it does not
   decide permissibility. Until a standard is chosen, the universe is undefined
   and nothing downstream is actionable.
2. **A point-in-time universe with delisted names retained** (amendment A3),
   built from `LISTING_STATUS`. Without it every backtest carries survivorship
   bias silently.
3. **A per-band A1 result**, per the section above.

Unmet preconditions mean the programme has not started. They do not consume
slots and they do not end it.

---

## What ending looks like

The programme reaches `PROGRAMME_CLOSED`, the terminal lifecycle state in Part 5
of the framework.

- The result is written up with the same detail a positive would have received.
- `STRATEGY_EDGE_DEMONSTRATED` in `src/intradyne/core/config.py` stays `False`,
  and the engine continues to refuse to start the trading loop.
- No approach five. No "one more variation". No re-run with adjusted windows.

**Stopping is a legitimate outcome and does not require a fifth approach to
justify it.** The system is complete as an engineering artifact and correctly
declines to trade; that is where it stays.

---

## What a positive result triggers

Not deployment.

A passing approach earns **forward paper measurement**, on live data the
selection never touched. Flipping `STRATEGY_EDGE_DEMONSTRATED` is a claim that
an edge has been demonstrated and must ship with the measurement that
demonstrates it -- not with this document, and not with a backtest.

Note also that A1 and A2 passing is not evidence of an edge. They establish only
that the arithmetic is not hopeless and that an effect of a given size could be
detected if it existed. US equities are the most competed market there is, and
better instruments do not manufacture an effect.

---

## What would legitimately reopen this

**A new idea is not a reason.** That is the whole point.

Reopening requires a change in the world rather than in the researcher:
materially lower execution costs, a data source that makes something previously
unmeasurable measurable, or a structural change in market access. And it
requires a new stop rule, committed in advance, exactly like this one.

---

## The commitment

Four approaches. A1 failing on every band ends it early. A variation of a failed
approach is that same approach, already spent. Looking at interim results spends
the slot whether or not the test is finished.

**When the fourth approach returns its verdict, the search stops. Whatever it
shows.**

That sentence is the entire mechanism. Its equivalent -- *"This is the last
crypto test. Whatever it shows, the search stops."* -- was written before the
test that closed crypto, and it is the only reason crypto is closed today rather
than still being re-litigated one variation at a time.
