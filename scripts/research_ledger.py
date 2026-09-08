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


# --- markdown rendering ---------------------------------------------------
#
# `--list` prints to a terminal, which means the record is only visible to
# someone who thinks to run the script. The point of moving this ledger into
# `docs/` was that the evidence survives a clone and shows up in review, and a
# JSONL file does neither for a reader: GitHub renders it as a wall of JSON and
# Obsidian cannot render it at all. So the same records get a committed
# markdown view.

#: Verdicts that mean the test answered, ordered worst-news-first. Anything
#: not listed still renders; this only fixes the order of the summary.
_VERDICT_ORDER = [
    "precondition_failure",
    "negative",
    "inconclusive",
    "positive",
]


def _fmt_value(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.6g}"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _prereg_link(ref: str) -> str:
    """Render `docs/X.md@abc1234` as a link plus the commit it was pinned at.

    The commit is the load-bearing half: a pre-registration is only worth
    anything if you can see the version that existed before the run, so it is
    shown rather than hidden behind the link.
    """
    if not ref:
        return "_none recorded_"
    path, _, commit = ref.partition("@")
    # Links are relative to docs/, where this file is written, so they resolve
    # in Obsidian and on GitHub alike.
    name = path.rsplit("/", 1)[-1]
    link = f"[{name}]({name})"
    return f"{link} @ `{commit}`" if commit else link


def render_markdown(
    records: List[Dict[str, Any]],
    chain: Optional[tuple] = None,
    path: str = DEFAULT_PATH,
) -> str:
    """The research record as a committed document.

    `chain` is the `(ok, index, message)` from `verify`. It is rendered at the
    top rather than omitted on success, because a log that only mentions its
    integrity when broken teaches a reader to assume silence means intact --
    and this record's whole purpose is that a removed negative result stays
    visible.
    """
    out: List[str] = []
    w = out.append

    w("# Research log")
    w("")
    w(f"Every recorded research run, newest first. Generated from `{path}` by")
    w("`python scripts/research_ledger.py --write`. **Do not edit by hand** --")
    w("regenerating overwrites this file.")
    w("")

    if chain is not None:
        ok, idx, msg = chain
        if ok:
            n = len(records)
            w(f"**Chain intact.** {n} run{'' if n == 1 else 's'} verified.")
        else:
            w(f"> [!WARNING] **Chain BROKEN at record {idx}.** {msg}")
            w(">")
            w("> A run was altered or removed after it was written. Treat every")
            w("> entry from that point on as unverified until this is explained.")
        w("")

    if not records:
        w("No runs recorded yet.")
        w("")
        return "\n".join(out) + "\n"

    counts = summarise(records)
    known = [v for v in _VERDICT_ORDER if v in counts]
    other = sorted(v for v in counts if v not in _VERDICT_ORDER)
    w("## Verdicts")
    w("")
    w("| verdict | runs |")
    w("| --- | ---: |")
    for v in known + other:
        w(f"| {v} | {counts[v]} |")
    w("")

    caveats = sum(1 for r in records if r.get("dirty") or r.get("backfilled"))
    if caveats:
        n = len(records)
        w(
            f"{caveats} of {n} run{'' if n == 1 else 's'} "
            f"{'carries' if caveats == 1 else 'carry'} a provenance caveat; "
            "see the notes on each."
        )
        w("")

    w("## Runs")
    w("")
    w("| date | verdict | script | commit | pre-registration |")
    w("| --- | --- | --- | --- | --- |")
    for r in sorted(records, key=lambda x: str(x.get("ts", "")), reverse=True):
        flags = ""
        if r.get("dirty"):
            flags += " ⚠"
        if r.get("backfilled"):
            flags += " †"
        prereg = str(r.get("preregistration", ""))
        name = prereg.partition("@")[0].rsplit("/", 1)[-1]
        w(
            f"| {str(r.get('ts', ''))[:10]} "
            f"| {r.get('verdict', '?')}{flags} "
            f"| `{r.get('script', '?')}` "
            f"| `{str(r.get('commit', ''))[:8]}` "
            f"| {name or '—'} |"
        )
    w("")
    if any(r.get("dirty") for r in records):
        w("⚠ ran from a tree with uncommitted changes, so it cannot be")
        w("reproduced from its commit alone.")
        w("")
    if any(r.get("backfilled") for r in records):
        w("† provenance reconstructed after the fact rather than captured at")
        w("runtime, which is weaker evidence than a contemporaneous record.")
        w("")

    w("## Detail")
    w("")
    for r in sorted(records, key=lambda x: str(x.get("ts", "")), reverse=True):
        w(f"### {str(r.get('ts', ''))[:10]} — `{r.get('script', '?')}`")
        w("")
        w(f"**Verdict:** {r.get('verdict', '?')}")
        w("")
        w(f"- run `{r.get('run_id', '?')}`")
        commit = str(r.get("commit", ""))
        dirty = (
            " — **dirty tree**, not reproducible from this commit alone"
            if r.get("dirty")
            else ""
        )
        w(f"- commit `{commit[:8]}`{dirty}")
        if r.get("seed") is not None:
            w(f"- seed `{r.get('seed')}`")
        w(f"- pre-registration: {_prereg_link(str(r.get('preregistration', '')))}")
        argv = r.get("argv") or []
        if argv:
            w(f"- argv: `{' '.join(str(a) for a in argv)}`")
        if r.get("backfilled"):
            note = str(r.get("note", "")) or "reconstructed after the fact"
            w(f"- **† backfilled:** {note}")
        w("")

        for label, key in (("Parameters", "params"), ("Inputs", "inputs")):
            d = r.get(key) or {}
            if d:
                w(f"**{label}**")
                w("")
                for k in sorted(d):
                    w(f"- `{k}`: {_fmt_value(d[k])}")
                w("")

        s = r.get("summary") or {}
        if s:
            w("**Result**")
            w("")
            w("| field | value |")
            w("| --- | ---: |")
            for k in sorted(s):
                w(f"| {k} | {_fmt_value(s[k])} |")
            w("")

    return "\n".join(out) + "\n"


#: Written next to the ledger it renders, so a reader who finds one finds both.
DEFAULT_MARKDOWN = "docs/RESEARCH_LOG.md"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--json", action="store_true", help="Emit records as JSON")
    ap.add_argument(
        "--write",
        nargs="?",
        const=DEFAULT_MARKDOWN,
        metavar="PATH",
        help=f"Render the record to markdown (default {DEFAULT_MARKDOWN})",
    )
    args = ap.parse_args(argv)

    rs = runs(args.path)

    if args.write:
        # The chain is verified here rather than trusted: the document reports
        # its own integrity, and a broken chain must still render -- refusing
        # to write would hide the very thing worth seeing.
        chain = verify(args.path)
        text = render_markdown(rs, chain=chain, path=args.path)
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(rs)} runs)")
        if not chain[0]:
            print("  chain is BROKEN; the document says so")
            return 1
        return 0

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
