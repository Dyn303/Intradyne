"""Resolve CUSIPs to tradeable tickers, via OpenFIGI.

Precondition P1 of `docs/SLOT_1_PREREGISTRATION.md` asks for prices joinable to
the SPUS universe by CUSIP *through renames and delistings*. I had assumed that
required a paid security master. It does not, at least for the mapping half:
OpenFIGI is Bloomberg's symbology service, it is free, it needs no key at low
volume, and it answers CUSIP queries directly.

## Why the mapping half is the interesting half

A rename is invisible in the SPUS timeline, because N-PORT records CUSIPs and a
CUSIP survives a ticker change. So the timeline is already rename-proof; what
was missing was a way to turn those CUSIPs into symbols a price source
understands. That is this module.

What it does *not* solve is whether a price source will then serve a delisted
symbol. A CUSIP that resolves to a ticker nobody will quote is still a hole in
the panel, and it shows up in `spus_panel.Coverage` rather than here.

## Caching is not an optimisation here

Results are cached to disk because re-running should not re-query: OpenFIGI
allows 25 requests a minute without a key, the universe is 322 CUSIPs, and a
probe that costs two minutes every time will not be run as often as it should
be. The cache also makes the mapping reproducible, which matters when the
result feeds a pre-registered test.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ENDPOINT = "https://api.openfigi.com/v3/mapping"
#: Committed evidence, not runtime state. Tickers drift through renames and
#: delistings, so the mapping a panel was built against travels with it --
#: the reason docs/spread_measurements.json lives there too.
CACHE = Path("docs/cusip_ticker_map.json")
#: Without an API key OpenFIGI allows 25 requests a minute and 10 jobs each.
#: With a key it is far higher, but a key is one more thing to hold and this
#: universe is small enough not to need one.
JOBS_PER_REQUEST = 10
REQUESTS_PER_MINUTE = 25


@dataclass(frozen=True)
class Mapping_:
    cusip: str
    ticker: Optional[str]
    name: Optional[str]
    exchange: Optional[str]

    @property
    def resolved(self) -> bool:
        return bool(self.ticker)


def _load_cache(path: Path) -> Dict[str, Dict[str, Optional[str]]]:
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _post(batch: Sequence[str], api_key: Optional[str], timeout: float) -> List[dict]:
    payload = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(), headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as f:
        out = json.load(f)
    return out if isinstance(out, list) else []


def _pick_us_listing(rows: List[dict]) -> dict:
    """The US composite listing, not merely the first row returned.

    OpenFIGI answers a CUSIP with every venue that lists the security -- HP Inc
    comes back with 172 rows, the German lines first. Taking `data[0]` gave
    `7HP` for HP, `8CW` for Crown Castle, `ABMDEUR` for Abiomed, and `ABG` for
    Cencora -- and `ABG` is Asbury Automotive, a different company entirely.
    Fed to a price source those would return EUR-denominated prices from
    Frankfurt, or the wrong issuer's history, and nothing downstream would
    notice.

    `exchCode == "US"` is the composite tape, which is what a US price source
    expects. Rows are otherwise left alone: a security with no US listing
    resolves to nothing, which is a true answer rather than a foreign
    substitute.
    """
    equities = [
        r
        for r in rows
        if isinstance(r, dict) and r.get("marketSector") == "Equity" and r.get("ticker")
    ]
    for want in ("US",):
        for r in equities:
            if r.get("exchCode") == want and r.get("securityType") == "Common Stock":
                return r
    for r in equities:
        if r.get("exchCode") == "US":
            return r
    return {}


def resolve(
    cusips: Iterable[str],
    cache_path: Path = CACHE,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
    refresh: bool = False,
) -> Dict[str, Mapping_]:
    """CUSIP to ticker, cached.

    A CUSIP that OpenFIGI cannot resolve is recorded as resolved-to-nothing
    rather than omitted, so a later run does not re-query it and so the count
    of failures is visible. Absence and failure are different facts, and
    conflating them is how a coverage figure quietly improves.
    """
    wanted = sorted({c.strip().upper() for c in cusips if c and c.strip()})
    cache = {} if refresh else _load_cache(cache_path)
    todo = [c for c in wanted if c not in cache]

    if todo:
        gap = 60.0 / REQUESTS_PER_MINUTE
        for i in range(0, len(todo), JOBS_PER_REQUEST):
            batch = todo[i : i + JOBS_PER_REQUEST]
            try:
                rows = _post(batch, api_key, timeout)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                # One failed batch must not lose the batches already done: the
                # cache is written below regardless, so a rate-limited run can
                # simply be repeated and will pick up where it stopped.
                print(
                    f"  batch {i // JOBS_PER_REQUEST}: {type(e).__name__} {e}",
                    flush=True,
                )
                break
            for cusip, row in zip(batch, rows):
                data = row.get("data") if isinstance(row, dict) else None
                pick = _pick_us_listing(data if isinstance(data, list) else [])
                cache[cusip] = {
                    "ticker": pick.get("ticker"),
                    "name": pick.get("name"),
                    "exchange": pick.get("exchCode"),
                }
            time.sleep(gap)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )

    return {
        c: Mapping_(
            cusip=c,
            ticker=(cache.get(c) or {}).get("ticker"),
            name=(cache.get(c) or {}).get("name"),
            exchange=(cache.get(c) or {}).get("exchange"),
        )
        for c in wanted
    }


def summarise(maps: Dict[str, Mapping_]) -> str:
    total = len(maps)
    ok = [m for m in maps.values() if m.resolved]
    lines = [
        f"CUSIPs queried   : {total}",
        f"resolved         : {len(ok)} ({100 * len(ok) / total:.1f}%)"
        if total
        else "nothing queried",
    ]
    missing = sorted(m.cusip for m in maps.values() if not m.resolved)
    if missing:
        lines.append(f"unresolved       : {len(missing)}  {missing[:8]}")
        lines.append(
            "  ^ these cannot be priced by any symbol-keyed source, so they "
            "count against the 80% precondition"
        )
    return "\n".join(lines)


__all__ = ["CACHE", "Mapping_", "resolve", "summarise"]
