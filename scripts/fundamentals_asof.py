#!/usr/bin/env python
"""Pick the fundamentals that were *public* on a given date.

`BALANCE_SHEET` returns reports keyed by `fiscalDateEnding` and nothing else.
Taking `reports[0]` therefore says "the most recent quarter that exists", which
is not the same as "the most recent quarter anyone could have known about".
IBM's figures for the quarter ending 2025-12-31 were published on 2026-01-28 --
a month later. A screen run against the period end assumes a month of
foresight, and a backtest built that way earns returns nobody could have.

`EARNINGS` supplies what is missing: `quarterlyEarnings` carries
`fiscalDateEnding`, `reportedDate` and `reportTime` back to the 1990s. Joining
on it turns a period end into a publication date.

Two details decide correctness rather than decorate it.

**`reportTime` moves the boundary by a day.** A post-market release on
2026-01-28 could not inform a trade until the 29th; a pre-market one could be
acted on that morning. Most releases in this data are post-market, so ignoring
the field leaks a day on the majority of filings -- small per filing, and
systematically in the profitable direction.

**A missing `reportedDate` must fail late, not early.** Where the join finds
nothing the fallback is a *conservative* lag, so the figures become available
later than they really did. Guessing early would reintroduce the leak this
module exists to close, quietly, on exactly the names whose data is worst.

The fallback is 90 days because that is the SEC filing deadline for a
non-accelerated filer's 10-K -- a statutory bound rather than a preference. The
observed lag is far shorter (IBM's median is around three weeks), so the
fallback is deliberately pessimistic and `observed_lags()` is provided to check
it against a real sample rather than trusting this sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: SEC 10-K deadline for a non-accelerated filer. Used only when the
#: publication date is unknown, and chosen to be late rather than plausible.
DEFAULT_LAG_DAYS = 90


def _d(v: Any) -> Optional[date]:
    s = str(v or "").strip()
    if not s or s.lower() in {"none", "null", "n/a", "-"}:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class Filing:
    """One reporting period, and when the market learned about it."""

    fiscal_end: date
    reported: Optional[date] = None
    report_time: str = ""

    def known_from(self, fallback_lag_days: int = DEFAULT_LAG_DAYS) -> date:
        """First date these figures could have informed a decision.

        A post-market release is not actionable until the following day. The
        next *session* would be more precise still, but that needs an exchange
        calendar; rolling to the next calendar day is never early, which is the
        property that matters here.
        """
        if self.reported is None:
            return self.fiscal_end + timedelta(days=fallback_lag_days)
        if self.report_time.strip().lower().startswith("post"):
            return self.reported + timedelta(days=1)
        return self.reported

    def lag_days(self) -> Optional[int]:
        if self.reported is None:
            return None
        return (self.reported - self.fiscal_end).days


def filings_from_earnings(payload: Optional[Mapping[str, Any]]) -> List[Filing]:
    """Parse `EARNINGS.quarterlyEarnings` into publication dates."""
    if not isinstance(payload, Mapping):
        return []
    out: List[Filing] = []
    for row in payload.get("quarterlyEarnings") or []:
        if not isinstance(row, Mapping):
            continue
        fe = _d(row.get("fiscalDateEnding"))
        if fe is None:
            continue
        out.append(
            Filing(
                fiscal_end=fe,
                reported=_d(row.get("reportedDate")),
                report_time=str(row.get("reportTime") or ""),
            )
        )
    out.sort(key=lambda f: f.fiscal_end)
    return out


def latest_known(
    filings: Sequence[Filing],
    on: date,
    fallback_lag_days: int = DEFAULT_LAG_DAYS,
) -> Optional[Filing]:
    """The most recent filing public on ``on``, or None if none were."""
    eligible = [f for f in filings if f.known_from(fallback_lag_days) <= on]
    return max(eligible, key=lambda f: f.fiscal_end) if eligible else None


def pick_report(
    reports: Sequence[Mapping[str, Any]],
    filings: Sequence[Filing],
    on: date,
    fallback_lag_days: int = DEFAULT_LAG_DAYS,
) -> Optional[Dict[str, Any]]:
    """The balance sheet that was public on ``on``.

    Reports carry only a period end, so each is matched to its filing to learn
    when it was published. A report with no matching filing is not discarded --
    it falls back to the conservative lag, because dropping it would silently
    shrink the universe on exactly the names with the thinnest data.

    Returns the report dict with two fields added, so downstream records carry
    their own provenance rather than relying on the caller to remember it:
    `_known_from` and `_as_of`.
    """
    by_end = {f.fiscal_end: f for f in filings}
    best: Optional[Dict[str, Any]] = None
    best_end: Optional[date] = None
    for rep in reports:
        fe = _d(rep.get("fiscalDateEnding"))
        if fe is None:
            continue
        filing = by_end.get(fe) or Filing(fiscal_end=fe)
        if filing.known_from(fallback_lag_days) > on:
            continue
        if best_end is None or fe > best_end:
            best, best_end = dict(rep), fe
    if best is None or best_end is None:
        return None
    filing = by_end.get(best_end) or Filing(fiscal_end=best_end)
    best["_known_from"] = filing.known_from(fallback_lag_days).isoformat()
    best["_as_of"] = on.isoformat()
    return best


def observed_lags(filings: Iterable[Filing]) -> Dict[str, Any]:
    """Real publication lags, for checking the fallback against evidence."""
    lags = sorted(x for x in (f.lag_days() for f in filings) if x is not None)
    if not lags:
        return {"n": 0}
    return {
        "n": len(lags),
        "min": lags[0],
        "median": lags[len(lags) // 2],
        "p95": lags[min(len(lags) - 1, int(len(lags) * 0.95))],
        "max": lags[-1],
    }


__all__ = [
    "DEFAULT_LAG_DAYS",
    "Filing",
    "filings_from_earnings",
    "latest_known",
    "observed_lags",
    "pick_report",
]
