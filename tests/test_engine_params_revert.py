"""Revert has to restore the parameters that were running *before* the last
apply. Nothing else is a revert.

The endpoint is the second half of Phase 2's parameter control, and a Revert
button that reports success while changing nothing is worse than no button:
the operator believes the system is back on the old configuration and stops
looking.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def applied(tmp_path, monkeypatch):
    """A client whose applies land in a recorder instead of a live engine.

    `_apply` raises 409 when no loop is running, so the engine call is stubbed;
    what these tests are about is which parameters get handed to it.
    """
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("ENGINE_ENABLED", "false")
    monkeypatch.setenv("API_AUTH_REQUIRED", "0")

    seen: list[dict] = []

    import intradyne.api.routes.engine as eng

    monkeypatch.setattr(eng, "_params_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(
        eng.engine_loop,
        "apply_params",
        lambda runtime: seen.append(json.loads(json.dumps(runtime))) or {"ok": True},
    )

    from intradyne.api.app import app

    with TestClient(app) as c:
        yield c, tmp_path, seen


def write_params(root: Path, marker: str) -> None:
    (root / "production_params.json").write_text(
        json.dumps({"risk": {"max_pos_pct": 0.1}, "marker": marker}),
        encoding="utf-8",
    )


def test_revert_restores_the_previous_parameters(applied):
    """The bug this file was written for.

    apply() copied production_params.json into the .prev backup *before*
    applying it -- but that file already held the new values, so the backup was
    a copy of what was being applied, not of what it replaced. Revert then
    re-applied the current parameters and reported {"reverted": true}.
    """
    client, root, seen = applied

    write_params(root, "OLD")
    assert client.post("/engine/params/apply").status_code == 200

    write_params(root, "NEW")
    assert client.post("/engine/params/apply").status_code == 200

    seen.clear()
    r = client.post("/engine/params/revert")
    assert r.status_code == 200
    assert r.json()["reverted"] is True
    assert seen, "revert applied nothing at all"
    assert seen[-1]["marker"] == "OLD", (
        f"revert re-applied {seen[-1]['marker']!r} instead of restoring OLD"
    )


def test_revert_rewrites_the_params_file_to_the_restored_values(applied):
    """A revert that changes the running engine but leaves the file holding the
    rejected parameters puts the next apply straight back onto them."""
    client, root, _ = applied

    write_params(root, "OLD")
    client.post("/engine/params/apply")
    write_params(root, "NEW")
    client.post("/engine/params/apply")
    client.post("/engine/params/revert")

    on_disk = json.loads((root / "production_params.json").read_text(encoding="utf-8"))
    assert on_disk["marker"] == "OLD"


def test_revert_before_any_apply_reports_that_plainly(applied):
    client, root, seen = applied
    write_params(root, "OLD")
    r = client.post("/engine/params/revert")
    assert r.status_code == 200
    assert r.json() == {"reverted": False, "reason": "no_backup"}
    assert seen == [], "nothing should have been applied"


def test_a_single_apply_leaves_nothing_to_revert_to(applied):
    """After one apply there is no earlier state. Reverting to the parameters
    just applied is not a revert, so it must not claim to be one."""
    client, root, seen = applied
    write_params(root, "ONLY")
    client.post("/engine/params/apply")

    seen.clear()
    r = client.post("/engine/params/revert")
    assert r.json()["reverted"] is False, (
        "revert claimed success with no previous configuration to return to"
    )
    assert seen == []


def test_two_reverts_do_not_walk_further_back(applied):
    """One level of undo. The second revert has nothing new to restore and
    must say so rather than flip-flopping between the two configurations."""
    client, root, seen = applied
    write_params(root, "OLD")
    client.post("/engine/params/apply")
    write_params(root, "NEW")
    client.post("/engine/params/apply")

    assert client.post("/engine/params/revert").json()["reverted"] is True
    seen.clear()
    second = client.post("/engine/params/revert").json()
    assert second["reverted"] is False, (
        f"second revert claimed to restore something: {second}"
    )
    assert seen == []
