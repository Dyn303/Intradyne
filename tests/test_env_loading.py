"""The API must read its own dotenv file.

`core/config.py` points pydantic-settings at the dotenv file, which is easy to
mistake for "the app reads it". It does not: that only populates a `Settings`
object, and none of the credential lookups go through `Settings`.
`configured_api_key()`, `telegram_auth.bot_token()` and the alerter all call
`os.getenv` directly.

So before `load_dotenv()` was added to the API entrypoint, following the
documented setup produced one of two silent outcomes: an app that refused to
boot complaining no credential was configured, or -- worse -- one that booted
with Mini App auth quietly disabled because the allowlist looked empty.

These run in a subprocess. Importing the app module twice in one process
re-registers the Prometheus collectors and blows up on duplicate timeseries,
and reloading it to observe an import-time side effect is exactly the kind of
test that breaks its neighbours.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

PROBE = textwrap.dedent(
    """
    import os
    import intradyne.api.app  # noqa: F401  -- import is the thing under test
    print("TOKEN=" + (os.getenv("TELEGRAM_BOT_TOKEN") or ""))
    print("ALLOW=" + (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or ""))
    print("KEY=" + (os.getenv("X_API_KEY") or ""))
    """
)


def run_probe(cwd: Path, env_overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **{k: v for k, v in __import__("os").environ.items()},
        "PYTHONPATH": SRC,
        "ENGINE_ENABLED": "false",
        "API_AUTH_REQUIRED": "0",
    }
    # The ambient environment must not leak in and fake a pass.
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "X_API_KEY"):
        env.pop(k, None)
    env.update(env_overrides or {})
    r = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"probe failed:\n{r.stdout}\n{r.stderr}"
    return dict(
        line.split("=", 1) for line in r.stdout.strip().splitlines() if "=" in line
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=from-the-dotenv-file\n"
        "TELEGRAM_ALLOWED_USER_IDS=5150,9999\n"
        "X_API_KEY=key-from-the-dotenv-file\n",
        encoding="utf-8",
    )
    return tmp_path


def test_credentials_in_the_dotenv_file_reach_the_environment(project):
    """The bug this was written for: they used to reach nothing."""
    got = run_probe(project)
    assert got["TOKEN"] == "from-the-dotenv-file"
    assert got["ALLOW"] == "5150,9999"
    assert got["KEY"] == "key-from-the-dotenv-file"


def test_an_explicit_environment_still_wins(project):
    """Docker, compose and CI set variables directly. A dotenv file that
    happens to be lying in the working directory must not override them."""
    got = run_probe(project, {"TELEGRAM_BOT_TOKEN": "from-the-real-environment"})
    assert got["TOKEN"] == "from-the-real-environment"
    # The keys the environment did not set still come from the file.
    assert got["ALLOW"] == "5150,9999"


def test_no_dotenv_file_is_not_an_error(tmp_path):
    """Most deployments have no such file; importing must not care."""
    got = run_probe(tmp_path)
    assert got == {"TOKEN": "", "ALLOW": "", "KEY": ""}


def test_mini_app_auth_switches_on_from_the_dotenv_file(project):
    """The end the user actually cares about: configuring the documented way
    makes `enabled()` true. It reported False before this fix."""
    probe = textwrap.dedent(
        """
        import intradyne.api.app  # noqa: F401
        from intradyne.api import telegram_auth
        print("ENABLED=" + str(telegram_auth.enabled()))
        print("ALLOWED=" + repr(sorted(telegram_auth.allowed_user_ids())))
        """
    )
    env = {
        **{k: v for k, v in __import__("os").environ.items()},
        "PYTHONPATH": SRC,
        "ENGINE_ENABLED": "false",
        "API_AUTH_REQUIRED": "0",
    }
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS"):
        env.pop(k, None)
    r = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "ENABLED=True" in r.stdout
    assert "ALLOWED=[5150, 9999]" in r.stdout
