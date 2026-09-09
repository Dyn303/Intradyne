"""The research log is a document about evidence, so what matters is what it
refuses to hide.

Two properties carry the weight. A broken hash chain must still render, and
must say so -- refusing to write would remove the only visible sign that a
negative result was deleted, which is the entire reason the chain exists. And a
backfilled run must not look like a contemporaneous one; provenance
reconstructed after the fact is weaker evidence, and presenting the two
identically is the "story told afterwards" failure the research framework was
written to prevent.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_ledger as rl  # noqa: E402


def make_run(**kw):
    base = {
        "event": "research_run",
        "run_id": "abc123456789",
        "ts": "2026-09-03T00:00:00+00:00",
        "script": "some_test",
        "argv": [],
        "commit": "1234567890abcdef",
        "dirty": False,
        "seed": 42,
        "preregistration": "docs/SOME_PREREGISTRATION.md@deadbee",
        "verdict": "negative",
        "params": {"window": "2019-11"},
        "inputs": {},
        "summary": {"sharpe": 0.12},
    }
    base.update(kw)
    return base


# ---- a broken chain must be visible --------------------------------------


def test_a_broken_chain_is_reported_in_the_document():
    md = rl.render_markdown([make_run()], chain=(False, 3, "hash mismatch at record 3"))
    assert "BROKEN" in md
    assert "record 3" in md
    assert "altered or removed" in md


def test_a_broken_chain_still_renders_the_runs():
    """Refusing to render would delete the evidence of the deletion."""
    md = rl.render_markdown(
        [make_run(script="the_run_that_survived")], chain=(False, 1, "mismatch")
    )
    assert "the_run_that_survived" in md


def test_an_intact_chain_is_stated_rather_than_left_silent():
    """A log that only mentions integrity when broken teaches the reader that
    silence means intact, which is exactly the wrong habit here."""
    md = rl.render_markdown([make_run()], chain=(True, -1, "ok"))
    assert "Chain intact" in md


def test_writing_a_broken_chain_exits_nonzero(tmp_path, monkeypatch):
    """CI and humans both need the failure to be loud."""
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text(
        json.dumps(make_run(hash="wrong", hash_prev="alsowrong")) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "LOG.md"
    code = rl.main(["--path", str(ledger), "--write", str(out)])
    assert code == 1, "a broken chain must not exit 0"
    assert out.exists(), "the document must still be written"
    assert "BROKEN" in out.read_text(encoding="utf-8")


# ---- weak provenance must not look strong --------------------------------


def test_a_backfilled_run_is_marked_and_explained():
    md = rl.render_markdown(
        [make_run(backfilled=True, note="reconstructed from PR #37")],
        chain=(True, -1, "ok"),
    )
    assert "†" in md
    assert "reconstructed from PR #37" in md
    assert "weaker evidence" in md


def test_a_dirty_run_is_marked_as_not_reproducible():
    md = rl.render_markdown([make_run(dirty=True)], chain=(True, -1, "ok"))
    assert "⚠" in md
    assert "uncommitted changes" in md


def test_a_clean_run_carries_no_warning_marks():
    """A legend for a mark no run carries reads as though something is wrong."""
    md = rl.render_markdown([make_run()], chain=(True, -1, "ok"))
    assert "⚠" not in md
    assert "†" not in md
    assert "provenance caveat" not in md


def test_the_preregistration_commit_is_shown_not_just_linked():
    """A pre-registration only means something if you can see the version that
    existed before the run."""
    md = rl.render_markdown([make_run()], chain=(True, -1, "ok"))
    assert "deadbee" in md
    assert "[SOME_PREREGISTRATION.md](SOME_PREREGISTRATION.md)" in md


def test_a_run_with_no_preregistration_says_so():
    md = rl.render_markdown([make_run(preregistration="")], chain=(True, -1, "ok"))
    assert "none recorded" in md


# ---- shape ---------------------------------------------------------------


def test_an_empty_ledger_produces_a_document_not_a_crash():
    md = rl.render_markdown([], chain=(True, -1, "ok"))
    assert "No runs recorded" in md


def test_runs_are_newest_first():
    old = make_run(ts="2026-01-01T00:00:00+00:00", script="older_run")
    new = make_run(ts="2026-09-03T00:00:00+00:00", script="newer_run")
    md = rl.render_markdown([old, new], chain=(True, -1, "ok"))
    assert md.index("newer_run") < md.index("older_run")


def test_verdict_counts_are_tallied():
    runs = [make_run(verdict="negative") for _ in range(3)]
    runs.append(make_run(verdict="positive"))
    md = rl.render_markdown(runs, chain=(True, -1, "ok"))
    assert "| negative | 3 |" in md
    assert "| positive | 1 |" in md


@pytest.mark.parametrize("n,expected", [(1, "1 run verified"), (2, "2 runs verified")])
def test_run_count_reads_correctly(n, expected):
    md = rl.render_markdown([make_run() for _ in range(n)], chain=(True, -1, "ok"))
    assert expected in md


def test_the_document_warns_against_hand_editing():
    """It is regenerated, so an edit is silently lost."""
    md = rl.render_markdown([make_run()], chain=(True, -1, "ok"))
    assert "Do not edit by hand" in md


def test_writing_creates_missing_parent_directories(tmp_path):
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text("", encoding="utf-8")
    out = tmp_path / "nested" / "deeper" / "LOG.md"
    assert rl.main(["--path", str(ledger), "--write", str(out)]) == 0
    assert out.exists()
