"""The walkthrough (examples/walkthrough) end to end in fake mode, so it doesn't rot: a real sopops process on a free
port, the onboarding push, 300 generated calls (backfill and the LiveKit adapter's replay), verdicts, suggestions,
release 2, and verify.py's checklist. Then the same database served again with sign-in on, which is what
`run_all.py --sign-in` does at the end. On Postgres with SOPOPS_TEST_DATABASE_URL, like the rest of the suite."""

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import AUTH_ENV, SOPC_BIN, fresh_db_url

WALKTHROUGH = Path(__file__).resolve().parents[1] / "examples" / "walkthrough"
MODEL_ENV = ("SOPOPS_MODEL_FAST", "SOPOPS_MODEL_REASONING", "TYPESAFE_API_KEY", "SOPOPS_JEV", "SOPOPS_DECISIONS", "SOPOPS_CHECK_REVIEW",
             "SOPOPS_CHECK_MODE", "HATCHET_CLIENT_TOKEN", "DATABASE_URL")
DEMO = {"email": "test@example.com", "password": "password123"}  # serve.py's DEMO_ACCOUNT
SESSIONS = "/v1/workspaces/walkthrough/sessions?limit=1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(http: httpx.Client, server: subprocess.Popen) -> None:
    for _ in range(300):
        if server.poll() is not None:
            pytest.fail(f"sopops stopped before it was up ({server.returncode})")
        with contextlib.suppress(httpx.HTTPError):
            if http.get("/healthz").status_code == 200:
                return
        time.sleep(0.2)
    pytest.fail("sopops didn't come up")


def serve_signed_in(env: dict) -> None:
    """What `run_all.py --sign-in` does once the steps are done: the same database again, behind a sign-in.

    The populated data has to survive the restart, the seeded account has to open it, and a stranger has to be able
    to sign up without an invite."""
    base = f"http://127.0.0.1:{env['WALKTHROUGH_PORT']}"
    server = subprocess.Popen([sys.executable, str(WALKTHROUGH / "serve.py"), "--sign-in"], env=env, cwd=WALKTHROUGH,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=base, timeout=60) as http:
            wait_up(http, server)
            assert http.get(SESSIONS).status_code == 401, "sign-in should be required now"
            assert http.post("/v1/auth/password/login", json=DEMO).status_code == 200, "the seeded account should sign in"
            assert len(http.get("/v1/workspaces/walkthrough/releases").json()) == 2, "both releases should survive the restart"
            assert http.get(SESSIONS).json()["items"], "the calls should survive the restart"
        with httpx.Client(base_url=base, timeout=60) as stranger:
            made = stranger.post("/v1/auth/sign-up", json={"email": "stranger@example.com", "name": "A Stranger",
                                                           "password": "strangerpass1", "team": "Stranger Co"})
            assert made.status_code == 200, f"open sign-up should take anyone: {made.status_code} {made.text[:300]}"
    finally:
        server.terminate()
        server.wait(10)


@pytest.mark.parametrize("onboard", ["proposal", "sopc"])
def test_walkthrough_in_fake_mode(tmp_path, onboard):
    """Onboarding from a coding agent's proposal, or from the team's existing sopc project; the rest is the same."""
    env = {k: v for k, v in os.environ.items() if k not in MODEL_ENV + AUTH_ENV}
    env.update(WALKTHROUGH_ONBOARD=onboard, WALKTHROUGH_PORT=str(free_port()), WALKTHROUGH_OUT=str(tmp_path / "out"), WALKTHROUGH_DATABASE_URL=fresh_db_url(tmp_path, "walkthrough.db"),
               WALKTHROUGH_MODELS="fake", SOPC_BIN=SOPC_BIN, SOPOPS_CACHE_DIR=str(tmp_path / "cache"))
    got = subprocess.run([sys.executable, str(WALKTHROUGH / "run_all.py"), "--stop", "--fast"], env=env, cwd=WALKTHROUGH,
                         capture_output=True, text=True, timeout=900)
    out = got.stdout + got.stderr
    assert got.returncode == 0, out[-6000:]
    assert "FAIL" not in got.stdout, out[-6000:]
    assert "checks passed" in got.stdout
    serve_signed_in(env)
