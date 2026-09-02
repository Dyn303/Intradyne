"""Check that a deployed stack actually serves what it claims to.

Run against a running instance: `python scripts/e2e_smoke.py`, or
`API_BASE=http://localhost:8080 python scripts/e2e_smoke.py` for the compose
stack. Exits non-zero if anything is wrong.

Three things this checks that the previous version did not, each because it
has actually gone wrong in this repo:

* **The dashboard is served.** It ships inside the Python package rather than
  as a separate build, so a Dockerfile that copies the wrong thing produces a
  perfectly healthy API serving no UI.

* **`/metrics` answers, and answers Prometheus text.** It has been silently
  shadowed twice -- once behind the risk router, once behind the static mount
  at "/". Both times every health check stayed green while metrics 404ed,
  because nothing checked it.

* **Credentials are sent when configured.** The old script sent none, so
  against any authenticated deployment every endpoint returned 401 and the
  script reported a broken system. A smoke test that cannot pass on a correctly
  configured production instance is one people learn to ignore.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional, Tuple

import httpx

# (path, description, extra check on the response)
Check = Tuple[str, str, Optional[Callable[[httpx.Response], Optional[str]]]]


def _is_dashboard(r: httpx.Response) -> Optional[str]:
    if "text/html" not in r.headers.get("content-type", ""):
        return f"expected HTML, got {r.headers.get('content-type')!r}"
    if "<title>Intradyne</title>" not in r.text:
        return "served a page, but not the dashboard"
    return None


def _is_prometheus(r: httpx.Response) -> Optional[str]:
    # A 200 is not enough: when the static mount shadowed this route it
    # answered 200 with an HTML page.
    if "text/plain" not in r.headers.get("content-type", ""):
        return f"expected Prometheus text, got {r.headers.get('content-type')!r}"
    return None


def _has_records(r: httpx.Response) -> Optional[str]:
    body = r.json()
    if not body.get("records"):
        return "the research record index is empty"
    return None


CHECKS: List[Check] = [
    ("/healthz", "liveness", None),
    ("/readyz", "readiness, including the database", None),
    ("/version", "build identity", None),
    ("/", "the dashboard", _is_dashboard),
    ("/metrics", "Prometheus metrics", _is_prometheus),
    ("/frontend/config", "frontend configuration", None),
    ("/engine/status", "engine state", None),
    ("/risk/status", "risk state and the halt", None),
    ("/portfolio", "positions and balances", None),
    ("/research/record", "the research record index", _has_records),
]


def main() -> int:
    base = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
    key = (os.getenv("X_API_KEY") or "").strip()
    headers = {"X-API-Key": key} if key else {}
    if not key:
        print("note: X_API_KEY unset; assuming this instance runs without auth")

    failures: List[str] = []
    with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
        for path, what, extra in CHECKS:
            url = base + path
            try:
                r = client.get(url)
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {path:22} unreachable: {type(exc).__name__}")
                failures.append(f"{path}: unreachable")
                continue

            if r.status_code != 200:
                hint = ""
                if r.status_code == 401:
                    hint = " (set X_API_KEY to match the deployment)"
                print(f"FAIL {path:22} {r.status_code}{hint}  -- {what}")
                failures.append(f"{path}: HTTP {r.status_code}")
                continue

            problem = None
            try:
                problem = extra(r) if extra else None
            except Exception as exc:  # noqa: BLE001
                problem = f"unparseable response: {type(exc).__name__}"

            if problem:
                print(f"FAIL {path:22} 200 but {problem}")
                failures.append(f"{path}: {problem}")
            else:
                print(f"ok   {path:22} {what}")

    if failures:
        print(f"\n{len(failures)} of {len(CHECKS)} checks failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nall {len(CHECKS)} checks passed against {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
