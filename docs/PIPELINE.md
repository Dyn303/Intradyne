# The nine-stage pipeline

A design for how a candidate becomes an order, and the three gates it has to
get past.

**This is a design document, not a build order.** Five of the nine stages exist
and work; four are thin or missing. Nothing here authorises building the missing
ones — stages 4, 5 and 9 sit downstream of an edge that has not been
demonstrated, and `docs/EQUITY_PROGRAMME_STOP_RULE.md` still shows four of four
approaches unspent. The document exists so that when a stage is built, it is
built against a contract rather than against whatever the adjacent stage
happens to return.

The most useful part of it is therefore **Part 2, the contracts.** Boxes are
easy; what flows between them is where pipelines rot.

---

## Part 0 — Three gates, not one

Every stage below sits on one side or other of three independent refusals. They
fail closed separately and none of them substitutes for another.

| gate | scope | where | current |
|---|---|---|---|
| **G1 compliance** | per order | `risk/shariah.py` | refuses every equity — no screen record exists |
| **G2 edge** | per system | `core/config.py:STRATEGY_EDGE_DEMONSTRATED` | `False` — the loop will not start |
| **G3 live** | per deployment | `MODE` + `LIVE_TRADING_ENABLED` + the RUNBOOK items | armed refusal; paper only |

G1 is the one this pipeline threads through every stage. The Shariah screen is
carried as a **contract from stage 2 onward** — every candidate record has a
place for a screening verdict and its as-of date — while the gate itself stays
fail-closed. The pipeline can therefore be built and exercised end to end
*today*, on refusals, without a ruling and without pretending to have one.

---

## Part 1 — The stages

Each entry gives what the stage receives, what it emits, its state, and the one
rule that matters most for it.

### 1. Data fetch — **built**

`scripts/fetch_equity_bars.py`, `scripts/equity_pit_universe.py`

Bars and listing intervals. Emits `data/equities/{SYMBOL}_{interval}.csv` and
`docs/equity_listings.csv`.

> **The rule:** the universe is asked *as of a date*, never *as of now*. A
> listing is a `SYMBOL@ipoDate` interval and a ticker is only a label on it —
> 305 US symbols have been reassigned to unrelated companies, so keying by
> ticker splices two firms into one price series.

### 2. Market scanner — **built**

`scripts/screen_equities.py`, `scripts/equity_liquidity.py`

Reduces the universe to today's candidates: instrument type, price band, dollar
volume, liquidity floor, then the Shariah worksheet.

> **The rule:** live screening only. Movers choose candidates *to trade
> forward*. Using them to define a backtest universe is selection on the
> outcome, and is the failure amendment A3 exists to prevent.

### 3. Analyst — **thin, but no longer leaking**

Fundamentals are fetched inside the screener; there is no separate layer, and
`NEWS_SENTIMENT` is available and unused.

The look-ahead leak this section used to describe is closed.
`scripts/fundamentals_asof.py` joins each `BALANCE_SHEET` period to its
publication date from `EARNINGS.quarterlyEarnings`, and the screener now selects
the newest report that was *public* on the screening date rather than the newest
that exists. A post-market release rolls forward a day, because figures released
after the close cannot inform that session. Where a publication date is missing
the fallback is 90 days -- the SEC deadline for a non-accelerated filer's 10-K
-- so unknown data becomes available later than it really did, never earlier.

> **The rule:** every fundamental carries the date it *became public*, not its
> fiscal period end. IBM's 2025-12-31 figures were published 2026-01-28, so a
> screen run on 2026-01-15 against the period end assumes a month of foresight.
> Every screened record now carries three dates -- period end, known-from, and
> the date screened against -- because a ratio cannot be audited from any one
> of them.

### 4. Signal generator — **bridged**

`engine/strategies/{momentum,meanrev,ml}.py` implement `on_tick` for the live
router. The research library in `scripts/strategy_search.py` is ~50 vectorised
numpy masks over `Bars`.

`scripts/signal_bridge.py` removes the second implementation rather than
verifying it. `VectorSignal` holds a rolling buffer and calls the research
function itself, so the engine path *is* the research path and drift is
impossible by construction. `minimum_buffer()` measures how many bars each
signal needs before the streamed mask matches the offline one exactly — 60 for
the finite-window families, up to 240 for the EMA ones, measured rather than
assumed.

The same harness is a **look-ahead detector**, which is the more valuable half.
A streaming run has only the bars up to the current index by construction, so a
signal that disagrees at *every* buffer length is reading the future. Nothing in
this repo could previously test that mechanically, and the framework treats
look-ahead as a rejection condition rather than a review note.

> **The rule:** one signal, two runtimes, and a test that the second is the
> first. A rule whose streamed entries differ from its researched ones is
> either under-buffered or non-causal, and those need opposite responses.

### 5. Trade plan — **crypto-tuned**

`engine/risk.py` (`RiskManager`), `artifacts/production_params.json`.

Turns a signal into an intended order: size, entry, stop, target, horizon.

> **The rule:** the exit is stated before the entry. A plan without a stop is
> not a plan, and the measured stop overshoot — a "20 bps" stop realising about
> −26 bps once discretisation is counted — belongs in the plan rather than
> discovered in the fills.

### 6. Risk system — **built, solid**

`risk/guardrails.py` with `drawdown`, `flash_crash`, `kill_switch`,
`var_limit`, `shariah`. `gate_trade(req) -> (action, reasons, order)` where
action is one of **allow / block / halt**. A warning is not a fourth action: a
drawdown inside its threshold returns `allow` with the warning in `reasons` and
a `dd_warn` entry in the ledger, so the order proceeds and the concern is still
on the record.

> **The rule:** a refusal is recorded, not discarded. Two endpoints in this
> system once returned success without acting, which is worse than no control —
> the operator stops looking.

### 7. Monitoring & alert — **built**

`core/alerts.py` (`alert(event, payload)`), the dashboard, the Telegram Mini
App, `/metrics`, and the hash-chained ledger.

> **The rule:** the gate reason is the headline, not a footnote. A user should
> never have to wonder why nothing is happening, and "correctly declining to
> trade" is the state this system is usually in.

### 8. Decision memo — **built**

`docs/DECISION_MEMO_TEMPLATE.md`. Eight required sections, and a memo is
refused if any is empty -- not filled in vaguely, empty, because a section
nobody can complete is itself the finding.

> **The rule:** the memo is committed in the same change as the flag it
> justifies. A `STRATEGY_EDGE_DEMONSTRATED` diff with no memo beside it is a
> one-line change with no attached reasoning, which is the state a `git pull`
> can forget.

### 9. Execution — **built for paper, no equity broker**

`engine/execution.py`, `broker_paper.py`, `reconcile.py`. `broker_ccxt.py` is
crypto. Webull Malaysia's OpenAPI exists and nothing here targets it.

> **The rule:** orders come from the engine, through the gate. A manual order
> path is a way to bypass the risk checks by accident, and if a human is the
> transport then the gate must still run and still write the ledger entry.

---

## Part 2 — The contracts

What flows between stages, and the property each hop must preserve.

| hop | payload | must preserve |
|---|---|---|
| 1 → 2 | bars + listing intervals | as-of date; delisted names retained |
| 2 → 3 | candidate set, dated | screening verdict slot, empty is *refuse* not *pass* |
| 3 → 4 | per-name context | publication date of every fundamental, not period end |
| 4 → 5 | `(listing_id, ts, long, strength)` | identical entries in both runtimes |
| 5 → 6 | intended order + stop + target + horizon | exit defined before entry |
| 6 → 9 | `(action, reasons, order)` | reasons survive into the ledger |
| any → 7 | events | gate reason preserved verbatim |
| 4–6 → 8 | evidence | the pre-registration each number answers |

Two properties are pipeline-wide rather than per-hop:

**Identity.** `listing_id`, not `symbol`, from stage 1 to stage 9. The moment a
stage keys by ticker, the reused-symbol problem re-enters silently.

**Provenance.** Every record carries the as-of date it was computed against. A
pipeline that loses this cannot tell a backtest from a live decision, which is
how look-ahead gets in.

---

## Part 3 — The decision memo

The novel stage, and the one that pays for itself.

Stages 1–7 produce evidence. The memo is **what a human signs before the system
is permitted to act**, and it is the artifact that accompanies flipping
`STRATEGY_EDGE_DEMONSTRATED`. Without it, that flag is a one-line diff with no
attached reasoning — which is exactly the state a `git pull` can forget.

The template is `docs/DECISION_MEMO_TEMPLATE.md`. A memo is refused unless it contains all of:

1. **The pre-registration it answers**, by commit hash. Criteria written after
   the fact are not criteria.
2. **The measurement**, with the null it cleared and the value of N in that
   best-of-N — counted across every approach that touched the data.
3. **The per-instrument breakdown and worst leave-one-out.** A pooled number
   carried by one name is not a strategy; the crypto record has a result whose
   edge fell from +3.45 t to 0.02 bps without a single instrument.
4. **The held-out result**, on data the selection never saw. It is the only
   gate in this project's history that has ever caught a false positive.
5. **The cost model used**, per band — 4.3 bps is a large-cap figure and a
   penny is 20 bps on a $5 share.
6. **The screening standard named**, with the as-of date of each record.
7. **The paper-versus-backtest comparison**, because the question is how much
   performance is lost moving from research to execution.
8. **A stated expectation**, so a later disappointment can be told from a later
   surprise.

The memo has one more property worth stating: **a negative memo is written with
the same care as a positive one.** Ten of them already exist in `MIGRATION.md`,
and they are why crypto is closed rather than re-litigated.

---

## Part 4 — What this deliberately does not design

- **A live equity broker adapter.** G2 is shut; building order submission for a
  strategy that does not exist is the clearest form of building apparatus
  instead of answers.
- **A manual trade-entry form.** It bypasses the gate by accident.
- **Automated promotion.** No measurement flips G2 on its own. A human signs
  the memo.
- **A second research programme.** The budget is four approaches, and the stop
  rule governs it.

---

## Part 5 — Order of work, if and when

Sequenced by what unblocks the four approaches, not by pipeline order:

1. ~~Stage 3's publication-date join~~ — **done**, see stage 3 above.
2. ~~Stage 4's bridge~~ — **done**, see stage 4 above.
3. ~~Stage 8's memo template~~ — **done**, and written before any approach
   produced numbers, which was the point of doing it early.
4. Stages 5 and 9 stay as they are until an approach clears.

The blocking precondition is unchanged and is not on this list, because no
amount of pipeline moves it: **the ruling on which screening standard applies.**
Everything above is designed to be correct when it arrives and to refuse safely
until it does.
