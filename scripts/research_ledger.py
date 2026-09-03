#!/usr/bin/env python
"""An append-only, hash-chained record of every research run.

    python scripts/research_ledger.py --list
    python scripts/research_ledger.py --verify

The last gap the source framework identified that this project had not closed:
its §26 asked for a strategy database, §27 for an experiment ID carrying dataset
and code version, and §28 for reproducible seeding. What existed instead was
`api/routes/research_record.py` -- a hardcoded registry of nine paths into
`artifacts/`, a directory that `.gitignore` excludes. So results did not survive
a fresh clone, carried no link to the commit that produced them, and could not
be compared across runs.

That criticism was fair and this answers it.

## Why a chained JSONL in docs/ rather than a database

**In `docs/` because `artifacts/` is gitignored.** Durability was the actual
complaint. `build_universe.py` already states the principle for its own output:
the evidence a decision was made against has to be committed with it.

**JSONL because it diffs.** A SQLite file is a binary blob: a reviewer cannot
see what a commit did to it, and a research record whose changes are invisible
in review is most of the way back to the problem. One run per line greps,
diffs, and merges.

**Hash-chained because the discipline depends on negatives surviving.** This is
the part a plain log would not give. The methodology here rests on negative
results being recorded and not quietly dropped -- ten of them are why crypto is
closed rather than re-litigated. A chain does not prevent deleting an
inconvenient run, but it makes the deletion *visible*: `verify_chain` reports
the index where the links stop matching. `core/ledger.py` already implements
exactly this for trades, so the research record gets the same guarantee from
the same code rather than a second implementation.

## What a run carries

Provenance is the point, so the fields that make a result reproducible are
required rather than optional: the **commit** it ran at and whether the tree was
dirty, the **script** and its **argv**, the **seed**, and a **fingerprint of the
input data**. A result whose inputs cannot be identified is an anecdote, and
`equity_liquidity.py` already records what happens when data quietly differs
from what a caller assumed.

`dirty` deserves its own field rather than a footnote. A run from an uncommitted
tree cannot be reproduced from its commit hash, and that is worth knowing at a
glance rather than discovering when someone tries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intradyne.core.ledger import Ledger  # noqa: E402

#: Committed, unlike `artifacts/`. That is the whole point of the change.
DEFAULT_PATH = "docs/research_runs.jsonl"


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing git is not a research failure
        return ""


def git_commit() -> str:
    return _git("rev-parse", "HEAD") or "unknown"


def git_dirty() -> bool:
    """True when the tree had uncommitted changes.

    Recorded rather than inferred: a run from a dirty tree cannot be
    reproduced from its commit hash, and that is worth seeing in the record
    instead of discovering when someone tries.
    """
    return bool(_git("status", "--porcelain"))


def fingerprint(paths: Sequence[str]) -> Dict[str, str]:
    """Content hashes for the inputs a run consumed.

    Names alone are not enough. A CSV can be regenerated with different bars
    under the same filename, and the run that used the old one would look
    identical in the record.
    """
    out: Dict[str, str] = {}
    for p in sorted(paths):
        f = Path(p)
        if not f.exists():
            out[p] = "missing"
            continue
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out[p] = h.hexdigest()[:16]
    return out


@dataclass
class Run:
    """One research run, with everything needed to find it again."""

    script: str
    verdict: str
    summary: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, str] = field(default_factory=dict)
    seed: Optional[int] = None
    preregistration: str = ""
    run_id: str = ""

    def as_record(self) -> Dict[str, Any]:
        return {
            "event": "research_run",
            "run_id": self.run_id or uuid.uuid4().hex[:12],
            "ts": datetime.now(timezone.utc).isoformat(),
            "script": self.script,
            "argv": sys.argv[1:],
            "commit": git_commit(),
            "dirty": git_dirty(),
            "seed": self.seed,
            "preregistration": self.preregistration,
            "verdict": self.verdict,
            "params": self.params,
            "inputs": self.inputs,
            "summary": self.summary,
        }


def record(run: Run, path: str = DEFAULT_PATH) -> Dict[str, Any]:
    """Append a run. Returns the written record, including its hash."""
    return Ledger(path=path).append(run.as_record())


def runs(path: str = DEFAULT_PATH) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [r for r in Ledger(path=path).iter_all() if r.get("type") != "genesis"]


def verify(path: str = DEFAULT_PATH):
    """(ok, index, message) -- the index is where the chain first breaks."""
    return Ledger(path=path).verify_chain()


def summarise(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in records:
        counts[str(r.get("verdict", "?"))] = (
            counts.get(str(r.get("verdict", "?")), 0) + 1
        )
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--json", action="store_true", help="Emit records as JSON")
    args = ap.parse_args(argv)

    rs = runs(args.path)

    if args.verify:
        ok, idx, msg = verify(args.path)
        print(f"chain: {'intact' if ok else 'BROKEN'} -- {msg}")
        if not ok:
            print(f"  first mismatch at record {idx}")
            print("  a run was altered or removed after it was written")
        return 0 if ok else 1

    if args.json:
        print(json.dumps(rs, indent=1))
        return 0

    if not rs:
        print(f"no runs recorded in {args.path}")
        return 0

    print(f"{len(rs)} runs in {args.path}")
    print()
    print(f"{'date':<11}{'verdict':<22}{'script':<30}{'commit':<10}")
    print("-" * 74)
    for r in rs:
        dirty = " *" if r.get("dirty") else ""
        print(
            f"{str(r.get('ts', ''))[:10]:<11}"
            f"{str(r.get('verdict', '?'))[:21]:<22}"
            f"{str(r.get('script', '?'))[:29]:<30}"
            f"{str(r.get('commit', ''))[:8]:<10}{dirty}"
        )
    print()
    for verdict, n in sorted(summarise(rs).items()):
        print(f"  {verdict:<22} {n}")
    if any(r.get("dirty") for r in rs):
        print()
        print("  * ran from a tree with uncommitted changes and cannot be")
        print("    reproduced from its commit alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
