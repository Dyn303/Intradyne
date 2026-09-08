"""A `PriceSource` over the resolution cascade, routed by whether a name died.

`spus_panel.PriceSource` has existed since #69 as a Protocol with no
implementation, and the P3 figures quoted so far were measured by hand. This is
the implementation, and it exists because the two halves of the universe need
different providers:

    still held (202 names)   yfinance     98.0% covered
    dropped   (120 names)    yfinance     53.1% covered   <- the P3 failure

yfinance stops serving a ticker once it delists, which is exactly the
population P3 measures. Alpha Vantage still answers: `TIME_SERIES_DAILY`
returns `ABMD` through 2023-01-03 and `ATVI` through 2023-10-13, their
delisting dates, takeover premium included. So each name is asked of the
provider that can answer it, rather than one provider being asked for
everything and its gaps being read as an absent universe.

## What the free tier actually gives, which is not enough

The sentence above is true and was, on its own, misleading -- it is recorded
here because the correction cost a run to find. `outputsize=full` is a
**premium** feature for `TIME_SERIES_DAILY`; the free tier serves `compact`,
the last 100 sessions. For a delisted name those are the 100 ending at its
delisting date.

Measured against the SPUS holding windows, that covers a **median 5.5%** of
the period each name was actually held, and nothing at all for eight of the
twenty-one -- ATVI, AVB, CTLT, CXO, DOC, KLG, WBA and TEL all delisted far
enough after the fund dropped them that the last 100 sessions miss the window
entirely.

Thirteen names would still show *some* overlap. A coverage test that asks
only "did any close come back" would count those as priced and let P3 pass at
85% on series averaging a twentieth of their window -- the same hollow pass,
one level down, that Amendment 1 introduced the dropped-tail measure to catch.
`window_coverage` exists so that cannot happen.

## Splits are checked, not assumed away

Alpha Vantage's adjusted endpoint is premium; the free one returns **raw**
closes, so a split inside the window would put a fake -50% return in the panel.
The free `SPLITS` endpoint gives the events, so this back-adjusts: every close
before a split is divided by its factor.

Most of these names never split during the window -- ABMD's only split was in
2000 -- so for most the adjustment is the identity, and the *check* is the
point. `splits_applied` records the names where it was not the identity,
because a correction nobody can see is indistinguishable from one that never
ran.

## Dividends are NOT adjusted, and that is a stated limitation

The free tier carries no adjusted close, and reconstructing total return from
the `DIVIDENDS` endpoint stacks a second approximation on this one. A
price-only series understates the return of high-yield names, which in a
REIT-bearing universe is not negligible. Worse, it is *asymmetric* here:
yfinance is asked with `auto_adjust=True` and so returns total return, while
the Alpha Vantage half does not -- meaning live and dead names are measured on
slightly different definitions.

This does not block P3, which asks only whether a name is priceable. It does
bear on any slot that trades on return, and closing it is a precondition of
slot 1 rather than something to discover afterwards.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Mapping, Optional

AV = "https://www.alphavantage.co/query"

#: Alpha Vantage's free daily allowance. Exceeded, it answers 200 with a Note
#: in the body rather than a 429, which is why `_av` inspects the payload
#: instead of trusting the status code.
FREE_TIER_PER_DAY = 25

#: How many daily sessions `outputsize=compact` returns. `full` is premium for
#: TIME_SERIES_DAILY, so this is the free ceiling on daily history per name --
#: about five months, against holding windows measured in years.
COMPACT_SESSIONS = 100

#: Frequency to endpoint. Weekly and monthly take no `outputsize` and return
#: the full life of a delisted name on the free tier -- ABMD comes back with
#: 1,208 weekly bars from 1999-11-12 to its delisting. Daily is the one that
#: is capped, and it is the one a daily panel needs.
AV_FUNCTION = {
    "daily": "TIME_SERIES_DAILY",
    "weekly": "TIME_SERIES_WEEKLY",
    "monthly": "TIME_SERIES_MONTHLY",
}


@dataclass(frozen=True)
class Resolution:
    """What a CUSIP resolved to, and whether that listing is dead.

    `delisted` routes the request rather than merely annotating it: asking
    yfinance for a dead ticker returns an empty frame indistinguishable from a
    network failure, and asking Alpha Vantage for everything would spend a
    25-a-day quota on the 202 names yfinance serves for nothing.
    """

    ticker: str
    delisted: Optional[str] = None


class CachedPrices:
    """Close series by CUSIP, fetched once and cached on disk.

    The cache is keyed by ticker rather than by request window, so a re-run
    costs no quota and an interrupted run resumes -- the shape
    `fetch_klines_archive.py` uses for its npz parts. A ticker the provider
    genuinely has nothing for is cached as an empty file on purpose:
    otherwise every re-run spends quota rediscovering the same absence, which
    on a 25-a-day budget means never getting past the first few failures.

    A *failed* request is never cached; see `series_for` for why that
    distinction is not academic.
    """

    def __init__(
        self,
        resolution: Mapping[str, Resolution],
        cache_dir: Path = Path("data/equities/daily"),
        api_key: str = "",
        sleep_s: float = 1.0,
        outputsize: str = "compact",
        frequency: str = "daily",
    ) -> None:
        self.resolution = dict(resolution)
        self.cache_dir = Path(cache_dir)
        self.api_key = api_key
        self.sleep_s = sleep_s
        # "full" needs a premium key; on the free tier it returns no data at
        # all rather than falling back, so the default has to be "compact".
        self.outputsize = outputsize
        if frequency not in AV_FUNCTION:
            raise ValueError(f"frequency must be one of {sorted(AV_FUNCTION)}")
        self.frequency = frequency
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.requests_made = 0
        self.splits_applied: Dict[str, int] = {}
        self.failures: Dict[str, str] = {}

    # -- provider calls -------------------------------------------------

    def _av(self, params: Dict[str, str], tries: int = 3) -> Optional[str]:
        """Alpha Vantage, as CSV. A quota breach is a 200 with a prose body."""
        import httpx

        query = dict(params)
        query["apikey"] = self.api_key
        query["datatype"] = "csv"
        for i in range(tries):
            try:
                r = httpx.get(AV, params=query, timeout=60.0)
            except Exception:
                time.sleep(2.0 * (i + 1))
                continue
            self.requests_made += 1
            if r.status_code != 200:
                time.sleep(2.0 * (i + 1))
                continue
            body = r.text.strip()
            # A CSV answer starts with its header. Anything else is a Note, a
            # premium notice or an error, all of which arrive as 200.
            if not body[:80].lower().startswith(("timestamp,", "effective_date,")):
                return None
            time.sleep(self.sleep_s)
            return body
        return None

    def _fetch_av_daily(self, ticker: str) -> Optional[Dict[date, float]]:
        """The last `OUTPUTSIZE` daily closes, or None if the request failed.

        `outputsize=full` is a **premium** feature for this endpoint -- the
        free tier answers it with an Information notice and no data, which is
        how a first run scored all 21 delisted names unpriceable while the
        same tickers returned bars perfectly well at `compact`.

        So this asks for what the free tier gives: the most recent 100
        sessions. For a delisted name those are the 100 ending at its
        delisting date, which is real history but a small slice of a
        multi-year holding window -- see `COMPACT_SESSIONS`.
        """
        params = {"function": AV_FUNCTION[self.frequency], "symbol": ticker}
        if self.frequency == "daily":
            # Only the daily endpoint takes outputsize; weekly and monthly
            # return everything and reject the parameter's premium form.
            params["outputsize"] = self.outputsize
        text = self._av(params)
        if not text:
            self.failures[ticker] = "alphavantage returned no series"
            return None
        out: Dict[date, float] = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out[date.fromisoformat(row["timestamp"])] = float(row["close"])
            except (KeyError, ValueError, TypeError):
                continue
        return self._apply_splits(ticker, out)

    def _apply_splits(
        self, ticker: str, series: Dict[date, float]
    ) -> Optional[Dict[date, float]]:
        """Back-adjust raw closes for splits falling inside the series.

        A split on date ``d`` with factor ``f`` means every close before ``d``
        is quoted in pre-split shares, so it is divided by ``f``. Applied
        newest-first so that successive splits compound correctly.

        A split exactly at the series start is ignored: there is nothing
        before it to adjust, and treating it as live would rescale the whole
        series against nothing.
        """
        if not series:
            return series
        text = self._av({"function": "SPLITS", "symbol": ticker})
        if not text:
            # Unknown is not the same as none. Refusing the series is the
            # conservative answer: an unadjusted split is a fabricated -50%
            # return, and P3 counting the name as priced would hide it.
            self.failures[ticker] = "split history unavailable; series withheld"
            return None
        events = []
        first, last = min(series), max(series)
        for row in csv.DictReader(io.StringIO(text)):
            try:
                d = date.fromisoformat(row["effective_date"])
                f = float(row["split_factor"])
            except (KeyError, ValueError, TypeError):
                continue
            if f > 0 and f != 1.0 and first < d <= last:
                events.append((d, f))
        if not events:
            return series
        self.splits_applied[ticker] = len(events)
        for d, f in sorted(events, reverse=True):
            series = {k: (v / f if k < d else v) for k, v in series.items()}
        return series

    def _fetch_yf(self, ticker: str) -> Optional[Dict[date, float]]:
        try:
            import yfinance as yf

            df = yf.Ticker(ticker).history(period="max", auto_adjust=True)
        except Exception as exc:  # pragma: no cover - network
            self.failures[ticker] = f"yfinance: {exc}"
            return None
        if df is None or df.empty or "Close" not in df:
            self.failures[ticker] = "yfinance returned no series"
            return None
        out: Dict[date, float] = {}
        for stamp, close in zip(df.index, df["Close"]):
            if close != close:  # NaN
                continue
            out[stamp.date() if hasattr(stamp, "date") else stamp] = float(close)
        return out

    # -- cache ----------------------------------------------------------

    def _path(self, ticker: str) -> Path:
        """Cache file for a ticker *at this frequency*.

        The frequency is in the name because it is now possible to fetch the
        same ticker at more than one: `TIME_SERIES_DAILY` is capped at 100
        sessions on the free tier while `WEEKLY` and `MONTHLY` return full
        history. Keyed by ticker alone, a weekly series would be served back
        to a caller that asked for daily, silently, and the panel would carry
        one bar a week for some names and one a day for others.
        """
        return self.cache_dir / f"{ticker.replace('/', '_')}.{self.frequency}.csv"

    def _cached(self, ticker: str) -> Optional[Dict[date, float]]:
        p = self._path(ticker)
        if not p.exists():
            return None
        out: Dict[date, float] = {}
        with p.open(encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh):
                if len(row) != 2:
                    continue
                try:
                    out[date.fromisoformat(row[0])] = float(row[1])
                except ValueError:
                    continue
        return out

    def _store(self, ticker: str, series: Mapping[date, float]) -> None:
        with self._path(ticker).open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            for d in sorted(series):
                writer.writerow([d.isoformat(), f"{series[d]:.6f}"])

    def series_for(self, ticker: str, delisted: bool) -> Dict[date, float]:
        """Cached closes for a ticker, fetching once on a miss.

        A *failed* request is never cached. The empty-file cache exists so a
        provider's genuine "no such series" is not rediscovered on every run,
        but a quota notice or a refused parameter is not that answer -- and
        caching it makes the failure permanent and invisible.

        This is not hypothetical: a first run asked for `outputsize=full`,
        which the free tier refuses, and wrote 21 empty files. Every later run
        would have honoured them and reported the same names unpriceable
        without spending a single request to find out otherwise.
        """
        got = self._cached(ticker)
        if got is not None:
            return got
        fetched = self._fetch_av_daily(ticker) if delisted else self._fetch_yf(ticker)
        if fetched is None:
            return {}
        self._store(ticker, fetched)
        return fetched

    # -- PriceSource ----------------------------------------------------

    def close_series(self, cusip: str, start: date, end: date) -> Mapping[date, float]:
        res = self.resolution.get(cusip)
        if res is None or not res.ticker:
            return {}
        full = self.series_for(res.ticker, res.delisted is not None)
        return {d: v for d, v in full.items() if start <= d <= end}


def window_coverage(series: Mapping[date, float], start: date, end: date) -> float:
    """Fraction of the window's weekdays for which a close exists.

    P3 asks whether the point-in-time universe can be *priced*, and a name
    with prices for a twentieth of the period it was held cannot be traded in
    a panel spanning that period. Membership in the panel is daily, so
    partial history is partial coverage rather than coverage.

    Weekdays approximate sessions: holidays make this a slight underestimate
    of the achievable fraction, which is the safe direction for a gate.
    """
    if end < start:
        return 0.0
    weekdays = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            weekdays += 1
        cursor += timedelta(days=1)
    if not weekdays:
        return 0.0
    have = sum(1 for d in series if start <= d <= end and d.weekday() < 5)
    return min(have / weekdays, 1.0)


__all__ = [
    "AV",
    "AV_FUNCTION",
    "COMPACT_SESSIONS",
    "FREE_TIER_PER_DAY",
    "CachedPrices",
    "Resolution",
    "window_coverage",
]
