"""The walkthrough (examples/walkthrough) end to end in fake mode, so it doesn't rot: a real sopops process on a free
port, the onboarding push, 300 generated calls (backfill and the LiveKit adapter's replay), verdicts, suggestions,
release 2, and verify.py's checklist. On Postgres with SOPOPS_TEST_DATABASE_URL, like the rest of the suite."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import AUTH_ENV, SOPC_BIN, fresh_db_url

WALKTHROUGH = Path(__file__).resolve().parents[1] / "examples" / "walkthrough"
MODEL_ENV = ("SOPOPS_MODEL_FAST", "SOPOPS_MODEL_REASONING", "TYPESAFE_API_KEY", "SOPOPS_JEV", "SOPOPS_DECISIONS", "SOPOPS_CHECK_REVIEW",
             "SOPOPS_CHECK_MODE", "HATCHET_CLIENT_TOKEN", "DATABASE_URL")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
