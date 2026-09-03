"""A research run must be findable again, and a deleted one must be visible.

The source framework's §26-28 asked for a strategy database, an experiment ID
carrying dataset and code version, and reproducible seeding. What this project
had was a hardcoded registry of nine paths into `artifacts/`, which `.gitignore`
excludes -- so results did not survive a clone and carried no link to the commit
that produced them.

Two properties carry the fix, and both fail silently if wrong.

**Provenance.** A run without its commit, inputs and seed is an anecdote. The
tests below assert those fields exist and are populated, because a ledger that
records only the answer is a slower way of writing the answer down.

**Tamper evidence.** The methodology rests on negative results surviving -- ten
of them are why crypto is closed rather than re-litigated. A chain cannot stop
someone deleting an inconvenient run, but it must make the deletion visible.
`test_editing_a_verdict_breaks_the_chain` is the one that matters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_ledger import (  # noqa: E402
    Run,
    fingerprint,
    record,
    runs,
    summarise,
    verify,
)


@pytest.fixture()
def led(tmp_path) -> str:
    return str(tmp_path / "runs.jsonl")


def _a_run(**kw) -> Run:
    base = dict(
        script="equity_band_a1.py",
        verdict="clears",
        seed=7,
        preregistration="docs/APPROACH_1_PREREGISTRATION.md@42d5bbc",
        params={"interval": "30min"},
        summary={"ratio_1d": 26.6},
    )
    base.update(kw)
    return Run(**base)  # type: ignore[arg-type]


# ---- tamper evidence ------------------------------------------------------


def test_editing_a_verdict_breaks_the_chain(led):
    """The property the whole design is for. Turning a failure into a pass
    after the fact must not be silent."""
    record(_a_run(verdict="fails"), path=led)
    record(_a_run(script="other", verdict="clears"), path=led)
    assert verify(led)[0] is True

    # Turn the recorded failure into a pass, in place.
    lines = Path(led).read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"fails"', '"clears"')
    Path(led).write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, idx, _ = verify(led)
    assert ok is False, "an edited verdict left the chain intact"
    assert idx is not None


def test_removing_a_run_breaks_the_chain(led):
    """Dropping an inconvenient negative is the failure mode worth catching."""
    for v in ("fails", "fails", "clears"):
        record(_a_run(verdict=v), path=led)
    lines = Path(led).read_text(encoding="utf-8").splitlines()
    del lines[1]
    Path(led).write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify(led)[0] is False


def test_an_untouched_ledger_verifies(led):
    for _ in range(3):
        record(_a_run(), path=led)
    ok, _, msg = verify(led)
    assert ok is True, msg


def test_appending_does_not_break_earlier_records(led):
    record(_a_run(), path=led)
    assert verify(led)[0] is True
    record(_a_run(script="second"), path=led)
    assert verify(led)[0] is True


# ---- provenance -----------------------------------------------------------


def test_a_run_records_the_commit_it_ran_at(led):
    rec = record(_a_run(), path=led)
    assert rec["commit"], "no commit recorded; the run cannot be reproduced"


def test_a_run_records_whether_the_tree_was_dirty(led):
    """A run from an uncommitted tree cannot be reproduced from its hash. That
    belongs in the record rather than being discovered later."""
    rec = record(_a_run(), path=led)
    assert isinstance(rec["dirty"], bool)


def test_a_run_records_its_seed(led):
    assert record(_a_run(seed=20191101), path=led)["seed"] == 20191101


def test_a_run_records_the_preregistration_it_answers(led):
    rec = record(_a_run(), path=led)
    assert "@" in rec["preregistration"], (
        "a pre-registration should be cited by commit; a path alone names a "
        "file that may have been edited"
    )


def test_each_run_gets_a_distinct_id(led):
    a = record(_a_run(), path=led)
    b = record(_a_run(), path=led)
    assert a["run_id"] != b["run_id"]


# ---- input fingerprints ---------------------------------------------------


def test_inputs_are_fingerprinted_by_content(tmp_path):
    f = tmp_path / "bars.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    first = fingerprint([str(f)])
    f.write_text("a,b\n9,9\n", encoding="utf-8")
    second = fingerprint([str(f)])
    assert first[str(f)] != second[str(f)], (
        "same filename, different bars, identical fingerprint -- the run that "
        "used the old data would look identical in the record"
    )


def test_a_missing_input_is_recorded_as_missing_not_skipped(tmp_path):
    got = fingerprint([str(tmp_path / "nope.csv")])
    assert got[str(tmp_path / "nope.csv")] == "missing"


def test_fingerprints_do_not_depend_on_argument_order(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("1", encoding="utf-8")
    b.write_text("2", encoding="utf-8")
    assert fingerprint([str(a), str(b)]) == fingerprint([str(b), str(a)])


# ---- reading back ---------------------------------------------------------


def test_runs_reads_back_what_was_written(led):
    record(_a_run(verdict="fails"), path=led)
    record(_a_run(script="two", verdict="clears"), path=led)
    got = runs(led)
    assert [r["verdict"] for r in got] == ["fails", "clears"]


def test_an_absent_ledger_reads_as_empty_not_an_error():
    assert runs("/nonexistent/path/runs.jsonl") == []


def test_summarise_counts_verdicts(led):
    for v in ("fails", "fails", "clears"):
        record(_a_run(verdict=v), path=led)
    assert summarise(runs(led)) == {"fails": 2, "clears": 1}


def test_every_record_is_valid_json_on_its_own_line(led):
    """JSONL rather than a database precisely so a reviewer can read a diff."""
    record(_a_run(), path=led)
    record(_a_run(), path=led)
    for line in Path(led).read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)


# ---- the committed ledger --------------------------------------------------


def test_the_committed_ledger_verifies():
    """The real one, in docs/. If this fails, a run was altered or removed."""
    p = Path(__file__).resolve().parents[1] / "docs" / "research_runs.jsonl"
    if not p.exists():
        pytest.skip("no runs recorded yet")
    ok, idx, msg = verify(str(p))
    assert ok is True, f"committed research ledger broken at {idx}: {msg}"


def test_backfilled_runs_say_so():
    """Provenance reconstructed from the merged record is not provenance
    captured at runtime, and conflating them would be the one dishonesty this
    ledger exists to prevent."""
    p = Path(__file__).resolve().parents[1] / "docs" / "research_runs.jsonl"
    if not p.exists():
        pytest.skip("no runs recorded yet")
    for r in runs(str(p)):
        if r.get("backfilled"):
            assert r.get("note"), "a backfilled run must explain where it came from"
