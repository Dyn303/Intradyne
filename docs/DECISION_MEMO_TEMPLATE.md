# Decision memo template

Copy to `docs/MEMO_<approach>_<date>.md`, fill it in, and commit it **in the
same change** as any flag it justifies. Delete the guidance in brackets as you go.

This is stage 8 of `docs/PIPELINE.md`. Stages 1–7 produce evidence; this is
**what a human signs before the system is permitted to act**, and it is the
artifact that accompanies flipping `STRATEGY_EDGE_DEMONSTRATED` in
`src/intradyne/core/config.py`.

Without it that flag is a one-line diff with no attached reasoning — which is
exactly the state a `git pull` can forget, and the reason the comment above it
already says that flipping it "should come with the measurement that
demonstrates it."

**A memo is refused if any section below is empty.** Not filled in vaguely —
empty. A section nobody can complete is the finding.

**Write a negative memo with the same care as a positive one.** Ten of them
exist in `MIGRATION.md`, and they are why crypto is closed rather than
re-litigated. Approach 1 of the equities programme has one too, and it is why
that slot returned unspent instead of being quietly re-attempted.

---

# Memo: <approach name>

**Verdict:** [positive / negative / inconclusive — state it here, first, before
any number. A reader who stops after one line should already know the answer.]

**Signed:** [who, and the date. A memo nobody signed is a report.]

**Flag this justifies:** [`STRATEGY_EDGE_DEMONSTRATED` → True, or "none — this
memo records a negative result", or "none — precondition failure".]

---

## 1. The pre-registration this answers

[Path and **commit hash**. The hash is the point: it proves the criteria
predate the numbers. A memo citing a pre-registration by path alone is citing
a file that may have been edited.]

[State which slot of the programme budget this spends, per
`docs/EQUITY_PROGRAMME_STOP_RULE.md`, and how many remain after it.]

[If the pre-registration was amended, name the amendment and confirm it was
committed before any result was seen. Amendment 1 of approach 1 is the worked
example — the universe clause specified a survey nobody would run.]

## 2. The measurement

[The headline number, as **excess over the matched benchmark** — never over
zero. State the benchmark.]

[The **null it cleared**, and the value of **N** in that best-of-N. N counts
every candidate that has touched this data across every approach, not the
number in this batch. A measurement that beats zero but not its null has not
beaten anything.]

[The significance, computed on the **period series** rather than on pooled
trades, with the effective breadth that justifies the clustering. State the
mean pairwise correlation.]

## 3. Per-instrument contribution, and the worst leave-one-out

[The full per-name table, and the single worst leave-one-out result.]

[This section exists because of one result in the crypto record: an
out-of-sample t of **+3.45** that became **0.02 bps** with a single instrument
removed. It was not a strategy; it was a long position in one memecoin wearing
an entry rule as a disguise. A pooled average carried by one name must be
visible here or the memo is misleading by omission.]

## 4. The held-out result

[Performance on data the selection never saw, and the size of that holdout.]

[The only gate in this project's history that has ever caught a false positive.
A crypto strategy cleared a day-clustered t of 4.58, beat its own null, and was
positive on 10 of 10 instruments — then lost money over the following eleven
months. Every in-sample guard passed. Only unseen data caught it.]

## 5. The cost model used

[Per band, not one figure. State the round trip, its components, and which
bands the result holds in.]

[4.3 bps is a **large-cap** number. A penny tick is 20 bps on a $5 share and
5 bps on a $20 one, so a result established on liquid names does not transfer
downward. `scripts/equity_band_a1.py` produces this; cite its output rather
than restating a remembered figure.]

## 6. The screening standard

[Which standard — AAOIFI, DJIM, S&P, MSCI — and the **as-of date** of every
screen record relied on.]

[A screen expires: ratios are recomputed when a company files, and
`risk/shariah.py` treats a record older than 120 days as no record at all. A
memo relying on stale screens is relying on a gate that would refuse the trade.]

## 7. Paper versus backtest

[The same metrics from both, side by side: win rate, profit factor, realised
return, drawdown, fill rate, slippage.]

[The question this answers is **how much performance is lost moving from
research to execution** — not whether the strategy works. A backtest and a
paper run that agree closely are evidence the execution model is honest; a wide
gap is the finding, whichever direction it runs.]

[If no paper run exists, say so plainly. A memo may be positive without one,
but it cannot then justify anything beyond starting one.]

## 8. Stated expectation

[What you expect to happen next, in numbers, before it happens.]

[This is what lets a later disappointment be told from a later surprise. A
strategy that underperforms a stated expectation is a measurement; one that
underperforms an unstated one is an argument.]

---

## What this memo does not claim

[Be explicit. The honest next step after a positive is **forward paper
measurement, not deployment**, and a memo that clears the research bar has
still not cleared:]

- **The compliance gate.** `risk/shariah.py` refuses any instrument without a
  current screen record, and it fails closed.
- **The live gate.** `LIVE_TRADING_GATE_OPEN` is a separate flag with its own
  checklist in RUNBOOK section 8 — a testnet soak, confirming a page reaches a
  human, rehearsing the halt, and setting exposure caps that default to
  disabled.
- **Settlement and venue reality.** A cash account caps intraday round-trips at
  roughly one per capital tranche per settlement cycle, regardless of how many
  signals fire.

## Known limitations

[Everything a reader would want to know and could not derive from the sections
above. Survivorship exposure, data gaps, regime coverage, sample composition.]

[Write the ones that weaken the case. A memo whose limitations section only
contains comfortable caveats is not a limitations section, and the reader most
likely to be harmed by that is the person who wrote it, six months later.]

---

## For a negative or inconclusive memo

Sections 1–8 are still required, with one substitution: where a section cannot
be completed **because the result was negative**, say what was measured and why
it fell short. Where it cannot be completed **because the data or the design
would not support it**, that is a precondition failure rather than a negative
result — say which, because they have different consequences for the budget.

A negative result spends its slot. A precondition failure does not, and the
ledger in the stop rule must be updated to say so.

State plainly what the result does **not** settle. The approach 1 memo is the
worked example: it established that the project cannot currently assemble a
survivorship-honest equity panel — a stronger and more useful finding than the
hypothesis it failed to test, and one that reaches further than the approach
that produced it.
