from __future__ import annotations

import os

import httpx


def main() -> int:
    base = os.getenv("API_BASE", "http://localhost:8000")
    urls = [
        f"{base}/healthz",
        f"{base}/readyz",
        f"{base}/frontend/config",
    ]
    ok = True
    with httpx.Client(timeout=5.0) as client:
        for u in urls:
            try:
                r = client.get(u)
                print(u, r.status_code)
                if r.status_code != 200:
                    ok = False
            except Exception as e:
                print(u, "ERROR", e)
                ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
