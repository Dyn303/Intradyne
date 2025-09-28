from __future__ import annotations

import os
from datetime import datetime
from fastapi.testclient import TestClient

from intradyne.api.app import app


client = TestClient(app)


def test_tuning_baseline_reads_files(tmp_path):
    # Prepare artifacts
    art = tmp_path / "artifacts"
    art.mkdir()
    tuned = {
        "profile": "auto",
        "created": datetime.utcnow().isoformat() + "Z",
        "params": {"ma_n": 30},
        "metric": "winrate",
        "score": 0.55,
    }
    applied = {
        "tuning_meta": {
            "metric": "winrate",
            "score": 0.50,
            "applied_at": datetime.utcnow().isoformat() + "Z",
        }
    }
    # Write files under working dir artifacts/
    (art / "tuned_profile.json").write_text(
        __import__("json").dumps(tuned), encoding="utf-8"
    )
    (art / "production_params.json").write_text(
        __import__("json").dumps(applied), encoding="utf-8"
    )
    # Chdir into tmp to let API read artifacts/ from cwd
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        r = client.get("/research/tuning/baseline")
        assert r.status_code == 200
        body = r.json()
        assert body["current_tuned"]["score"] == 0.55
        assert body["last_applied"]["score"] == 0.50
    finally:
        os.chdir(cwd)
