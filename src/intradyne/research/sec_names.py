"""Recover tickers OpenFIGI does not index, by exact name match against SEC.

OpenFIGI's CUSIP index has holes, and they are not where I expected. Querying
the 322 SPUS CUSIPs left 46 unresolved, and I read that as a survivorship wall
-- delisted names that no free source will serve. It was not. Honeywell, Lam
Research, Arista, Medtronic, Linde, NXP and a dozen others are actively traded
today; OpenFIGI simply returns no US composite row for their CUSIPs, only
currency-suffixed OTC lines like HONGBP and LRCXUSD on exchange code X1.

SEC publishes `company_tickers.json`: every current registrant with its ticker
and legal name. N-PORT gives the same legal names. Matching them recovers the
live companies and, correctly, recovers nothing for the genuinely delisted --
Abiomed and Twitter are not registrants any more, so they return nothing, which
is the true answer rather than a substitute.

## Exact matching only, and why

Fuzzy name matching is how a resolver puts the wrong company in a panel. The
first version of `cusip_map` took OpenFIGI's first row and mapped Cencora's
CUSIP to `ABG`, which is Asbury Automotive -- a different company whose price
history would have flowed into the panel unremarked. A scored fuzzy match makes
that failure more likely, not less, because it always returns something.

So this normalises both sides and requires an **exact** match on the result. A
name that does not match exactly resolves to nothing. That leaves recoverable
names on the table -- an acceptable price for never silently substituting an
issuer.

Normalisation strips punctuation and the corporate-form words that differ
between the two sources ("Inc", "Corp", "PLC", "Ltd", "Holdings"), because
N-PORT writes "Medtronic PLC" where SEC writes "Medtronic plc". It does not
attempt anything cleverer.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.request
from pathlib import Path
from typing import Dict, Optional

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE = Path("docs/sec_company_tickers.json")

#: Corporate-form words that differ between N-PORT and SEC filings for the same
#: issuer. Removed from both sides before comparison. Deliberately short: every
#: word here is a chance to collapse two distinct companies into one key.
_FORMS = (
    "INC",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "PLC",
    "LTD",
    "LIMITED",
    "NV",
    "SA",
    "AG",
    "GROUP",
    "HOLDINGS",
    "HLDGS",
    "THE",
)
_FORM_RE = re.compile(r"\b(" + "|".join(_FORMS) + r")\b")


def normalise(name: Optional[str]) -> str:
    """A comparable key for a company name.

    Returns an empty string for anything unusable, which never matches --
    an empty key must not become a bucket that collects unrelated issuers.
    """
    if not name:
        return ""
    s = name.upper().replace("&AMP;", "&")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _FORM_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_registry(
    cache_path: Path = CACHE, contact: Optional[str] = None, refresh: bool = False
) -> Dict[str, str]:
    """Normalised company name to ticker, from SEC's registrant list.

    A name that maps to more than one ticker is dropped rather than
    arbitrated. Two registrants sharing a normalised name means the
    normalisation was too aggressive for that pair, and picking one would be
    a silent coin flip over which company's prices enter the panel.
    """
    raw: Optional[bytes] = None
    if cache_path.exists() and not refresh:
        raw = cache_path.read_bytes()
    else:
        if not contact:
            raise ValueError(
                "SEC requires a contact in the User-Agent, e.g. "
                '"Jane Doe jane@x.com". A generic agent is refused with 403.'
            )
        req = urllib.request.Request(
            TICKERS_URL, headers={"User-Agent": contact, "Accept-Encoding": "gzip"}
        )
        with urllib.request.urlopen(req, timeout=30) as f:
            raw = f.read()
            if f.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)

    doc = json.loads(raw.decode("utf-8"))
    counts: Dict[str, set] = {}
    for entry in doc.values():
        if not isinstance(entry, dict):
            continue
        key = normalise(entry.get("title"))
        ticker = entry.get("ticker")
        if key and ticker:
            counts.setdefault(key, set()).add(str(ticker))
    return {k: next(iter(v)) for k, v in counts.items() if len(v) == 1}


def recover(
    unresolved_names: Dict[str, Optional[str]],
    registry: Dict[str, str],
) -> Dict[str, str]:
    """CUSIP to ticker for names the registry matches exactly.

    `unresolved_names` maps CUSIP to the company name N-PORT recorded. Only
    CUSIPs whose normalised name is present in the registry are returned; the
    rest are absent, which is how a caller distinguishes "recovered" from
    "still unknown".
    """
    out: Dict[str, str] = {}
    for cusip, name in unresolved_names.items():
        key = normalise(name)
        if key and key in registry:
            out[cusip] = registry[key]
    return out


__all__ = ["CACHE", "load_registry", "normalise", "recover"]
