"""A `PriceSource` over the resolution cascade, routed by whether a name died.

`spus_panel.PriceSource` has existed since #69 as a Protocol with no
implementation, and the P3 figures quoted so far were measured by hand. This is
the implementation, and it exists because the two halves of the universe need
different providers:

    still held (202 names)   yfinance     98.0% covered
    dropped   (120 names)    yfinance     53.1% covered   <- the P3 failure

yfinance stops serving a ticker once it delists, which is exactly the
population P3 measures. Alpha Vantage does not: `TIME_SERIES_DAILY` returns
`ABMD` through 2023-01-03 and `ATVI` through 2023-10-13, their delisting dates,
takeover premium included. So each name is asked of the provider that can
answer it, rather than one provider being asked for everything and its gaps
being read as an absent universe.

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
from datetime import date
from pathlib import Path
from typing import Dict, Mapping, Optional

AV = "https://www.alphavantage.co/query"

#: Alpha Vantage's free daily allowance. Exceeded, it answers 200 with a Note
#: in the body rather than a 429, which is why `_av` inspects the payload
#: instead of trusting the status code.
FREE_TIER_PER_DAY = 25


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
    `fetch_klines_archive.py` uses for its npz parts. A ticker that returned
    nothing is cached as an empty file on purpose: otherwise every re-run
    spends quota rediscovering the same absence, which on a 25-a-day budget
    means never getting past the first few failures.
    """

    def __init__(
        self,
        resolution: Mapping[str, Resolution],
        cache_dir: Path = Path("data/equities/daily"),
        api_key: str = "",
        sleep_s: float = 1.0,
    ) -> None:
        self.resolution = dict(resolution)
        self.cache_dir = Path(cache_dir)
        self.api_key = api_key
        self.sleep_s = sleep_s
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

    def _fetch_av_daily(self, ticker: str) -> Dict[date, float]:
        text = self._av(
            {"function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": "full"}
        )
        if not text:
            self.failures[ticker] = "alphavantage returned no series"
            return {}
        out: Dict[date, float] = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out[date.fromisoformat(row["timestamp"])] = float(row["close"])
            except (KeyError, ValueError, TypeError):
                continue
        return self._apply_splits(ticker, out)

    def _apply_splits(
        self, ticker: str, series: Dict[date, float]
    ) -> Dict[date, float]:
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
            return {}
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

    def _fetch_yf(self, ticker: str) -> Dict[date, float]:
        try:
            import yfinance as yf

            df = yf.Ticker(ticker).history(period="max", auto_adjust=True)
        except Exception as exc:  # pragma: no cover - network
            self.failures[ticker] = f"yfinance: {exc}"
            return {}
        if df is None or df.empty or "Close" not in df:
            self.failures[ticker] = "yfinance returned no series"
            return {}
        out: Dict[date, float] = {}
        for stamp, close in zip(df.index, df["Close"]):
            if close != close:  # NaN
                continue
            out[stamp.date() if hasattr(stamp, "date") else stamp] = float(close)
        return out

    # -- cache ----------------------------------------------------------

    def _path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.replace('/', '_')}.csv"

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
        got = self._cached(ticker)
        if got is not None:
            return got
        got = self._fetch_av_daily(ticker) if delisted else self._fetch_yf(ticker)
        self._store(ticker, got)
        return got

    # -- PriceSource ----------------------------------------------------

    def close_series(self, cusip: str, start: date, end: date) -> Mapping[date, float]:
        res = self.resolution.get(cusip)
        if res is None or not res.ticker:
            return {}
        full = self.series_for(res.ticker, res.delisted is not None)
        return {d: v for d, v in full.items() if start <= d <= end}


__all__ = ["AV", "FREE_TIER_PER_DAY", "CachedPrices", "Resolution"]
