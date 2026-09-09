"""Build a point-in-time Shariah-compliant equity universe from SEC N-PORT filings.

SPUS is the SP Funds S&P 500 Sharia Industry Exclusions ETF: the S&P 500 with
AAOIFI screening applied by a fund that files its holdings quarterly. Reading
those filings gives a compliance-screened universe that someone else audits,
and -- because every quarter is a separate filing -- one that can be
reconstructed *as it was*, not as it is today.

## Why point-in-time and not today's holdings list

Taking today's ~214 holdings and testing them over past years is survivorship
bias twice over: only companies that survived *and* stayed Shariah-compliant
appear, so every name dropped for rising debt, delisting or acquisition
silently vanishes from the sample. `scripts/point_in_time_universe.py` measured
a 43% divergence from exactly this error on a different universe. It is the
failure that returned Approach 1's slot unspent, and the reason this script
exists rather than a one-line holdings download.

## What this does not do

It records what SPUS held on each filing date. It does not decide what is
permissible -- the fund's adviser and its Shariah board did that, and this is a
transcription of their ruling, not a re-derivation of it. The same posture as
`scripts/build_universe.py`.

Note also that the SEC publishes N-PORT quarterly, so the universe steps in
quarters. A name added and dropped within one quarter is invisible here.

Usage:
    python scripts/spus_universe.py --contact "Your Name you@example.com"
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Tidal Trust I, the registrant SPUS files under.
CIK = "0001742912"
TICKER = "SPUS"
OUT = Path("docs/spus_universe_timeline.json")
#: SEC asks for no more than 10 requests a second and a contact in the
#: User-Agent so they can reach you if a script misbehaves. A generic agent
#: gets a 403 from www.sec.gov.
RATE_S = 0.15


class Edgar:
    def __init__(self, contact: str) -> None:
        self.ua = contact

    def get(self, url: str, tries: int = 3) -> Optional[str]:
        for k in range(tries):
            try:
                time.sleep(RATE_S)
                req = urllib.request.Request(
                    url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"}
                )
                with urllib.request.urlopen(req, timeout=30) as f:
                    raw = f.read()
                    if f.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return raw.decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                if k == tries - 1:
                    print(f"    give up on {url.rsplit('/', 1)[-1]}: {e}", flush=True)
                    return None
                time.sleep(1.5 * (k + 1))
        return None


def _nport_filings(ed: Edgar) -> List[Tuple[str, str]]:
    """Every NPORT-P accession for the registrant, recent and archived.

    The `recent` block holds only the latest ~1,000 filings; a trust filing one
    per fund per quarter fills that quickly, so the archived blocks matter and
    omitting them silently truncates the history.
    """
    out: List[Tuple[str, str]] = []
    base = ed.get(f"https://data.sec.gov/submissions/CIK{CIK}.json")
    if not base:
        return out
    doc = json.loads(base)
    blocks = [doc["filings"]["recent"]]
    for extra in doc["filings"].get("files", []):
        more = ed.get(f"https://data.sec.gov/submissions/{extra['name']}")
        if more:
            blocks.append(json.loads(more))
    for b in blocks:
        for form, date, acc in zip(b["form"], b["filingDate"], b["accessionNumber"]):
            if str(form).startswith("NPORT"):
                out.append((date, acc))
    return sorted(set(out))


def _is_ours(ed: Edgar, acc: str) -> bool:
    """Whether a filing belongs to SPUS.

    Checked against the 3 KB index-headers rather than the 150 KB holdings
    document: the trust files one N-PORT per fund per quarter, so scanning the
    full documents to find one series would download ~50 MB to keep 3%.
    """
    a = acc.replace("-", "")
    h = ed.get(
        f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{a}/{acc}-index-headers.html"
    )
    if not h:
        return False
    tickers = re.findall(r"CLASS-CONTRACT-TICKER-SYMBOL>\s*([^<\s]+)", h)
    return any(t.upper() == TICKER for t in tickers)


def _holdings(ed: Edgar, acc: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Equity positions from one filing, with the as-of date it reports.

    The filing date is not the as-of date -- N-PORT is filed up to 60 days
    after the period it describes, and using the filing date would date every
    holding wrongly by about two months.
    """
    a = acc.replace("-", "")
    x = ed.get(
        f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{a}/primary_doc.xml"
    )
    if not x:
        return "", []
    m = re.search(r"<repPdDate>(.*?)</repPdDate>", x, re.S)
    as_of = m.group(1).strip() if m else ""

    rows: List[Dict[str, Any]] = []
    for blk in re.findall(r"<invstOrSec>(.*?)</invstOrSec>", x, re.S):

        def tag(t: str) -> str:
            g = re.search(rf"<{t}>(.*?)</{t}>", blk, re.S)
            return g.group(1).strip() if g else ""

        # Equity only. A fund holding cash, futures or repo would otherwise
        # contribute rows that are not tradeable names.
        if tag("assetCat") not in ("EC", ""):
            continue
        ticker = tag("ticker")
        cusip = tag("cusip")
        if not ticker and not cusip:
            continue
        rows.append(
            {
                "ticker": ticker or None,
                "cusip": cusip or None,
                "name": tag("name") or None,
                "pct": float(tag("pctVal") or 0.0),
            }
        )
    return as_of, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--contact",
        required=True,
        help='SEC requires a contact in the User-Agent, e.g. "Jane Doe jane@x.com". '
        "A generic agent is refused with 403.",
    )
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    ed = Edgar(args.contact)
    print(f"== point-in-time {TICKER} universe from N-PORT ==", flush=True)

    filings = _nport_filings(ed)
    print(f"registrant has {len(filings)} NPORT filings", flush=True)

    ours: List[Tuple[str, str]] = []
    for i, (date, acc) in enumerate(filings):
        if _is_ours(ed, acc):
            ours.append((date, acc))
        if (i + 1) % 100 == 0:
            print(
                f"  scanned {i + 1}/{len(filings)}, {len(ours)} are {TICKER}",
                flush=True,
            )
    print(f"{TICKER} filings: {len(ours)}", flush=True)
    if args.limit:
        ours = ours[-args.limit :]

    timeline: Dict[str, Any] = {}
    if OUT.exists():
        timeline = json.loads(OUT.read_text(encoding="utf-8"))

    for date, acc in ours:
        as_of, rows = _holdings(ed, acc)
        if not rows:
            print(f"  {date}: no equity rows, skipped", flush=True)
            continue
        key = as_of or date
        timeline[key] = {
            "filed": date,
            "accession": acc,
            "n": len(rows),
            "holdings": sorted(rows, key=lambda r: -(r["pct"] or 0.0)),
        }
        print(f"  as of {key}: {len(rows)} equities (filed {date})", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(timeline, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    dates = sorted(timeline)
    print(f"\nwrote {OUT}: {len(dates)} quarters, {dates[0]} .. {dates[-1]}")

    # Identity is CUSIP, not ticker.
    #
    # N-PORT records holdings by CUSIP and name and leaves <ticker> empty --
    # 0 of 215 rows carry one. A first version of this summary compared
    # tickers, got two empty sets, and printed "0 names dropped" over six
    # years as though that were turnover data. CUSIP is also the better key:
    # it survives the ticker renames that broke earlier universe work.
    def ident(h: Dict[str, Any]) -> Optional[str]:
        return h.get("cusip") or h.get("ticker")

    ids = {d: {k for k in map(ident, timeline[d]["holdings"]) if k} for d in dates}
    if not any(ids.values()):
        print("")
        print("  NO IDENTIFIERS PARSED. A parse failure, not an empty universe.")
        print("  Do not read the turnover below as zero.")
        return 2

    if len(dates) > 1:
        first, last = ids[dates[0]], ids[dates[-1]]
        gone, added = first - last, last - first
        ever = set().union(*ids.values())
        always = set.intersection(*ids.values())
        print("")
        print(f"turnover, {dates[0]} -> {dates[-1]}:")
        print(
            f"  held then, not now : {len(gone):3}  "
            f"({100 * len(gone) / len(first):.0f}% of the original)"
        )
        print(f"  held now, not then : {len(added):3}")
        print(f"  names ever held    : {len(ever):3}")
        print(
            f"  names held always  : {len(always):3}  "
            f"({100 * len(always) / len(ever):.0f}%)"
        )
        print("")
        print(f"  A today's-holdings backtest would test those {len(always)} names")
        print(
            f"  instead of {len(ever)}, discarding "
            f"{100 - 100 * len(always) / len(ever):.0f}% of the universe."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
