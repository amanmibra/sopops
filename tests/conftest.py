import functools
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url, text

from sopops_server import Sopc, Store, create_app
from sopops_server.ai.lm import ROLE_ENV, set_ai
from alembic import command

from sopops_server.db import alembic_config, engine_from_url
from sopops_server.orm import metadata

FIXTURE = Path(__file__).parent / "fixtures" / "restaurants"
SOPC_BIN = os.environ.get("SOPC_BIN") or str(Path.home() / "Desktop/repos/sopc/target/release/sopc")
if not Path(SOPC_BIN).exists():
    SOPC_BIN = shutil.which("sopc")

WS = "/v1/workspaces/demo"


def pytest_collection_modifyitems(items):
    if not SOPC_BIN:
        skip = pytest.mark.skip(reason="needs a sopc binary with `validate --json` and `export` (set SOPC_BIN)")
        for item in items:
            item.add_marker(skip)


def read_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def expected(name: str) -> str:
    return (FIXTURE / "expected" / name).read_text()


AUTH_ENV = ("SOPOPS_GITHUB_OAUTH_CLIENT_ID", "SOPOPS_GITHUB_OAUTH_CLIENT_SECRET", "SOPOPS_GOOGLE_CLIENT_ID", "SOPOPS_GOOGLE_CLIENT_SECRET",
            "SOPOPS_AUTH_REQUIRED", "SOPOPS_ALLOWED_EMAIL_DOMAINS", "SOPOPS_PUBLIC_URL", "SOPOPS_PUBLIC_API_URL", "SOPOPS_EMAIL_DEV", "SOPOPS_SMTP_HOST", "SOPOPS_SMTP_PORT",
            "SOPOPS_SMTP_USER", "SOPOPS_SMTP_PASSWORD", "SOPOPS_SMTP_FROM", "SOPOPS_SUPPORT_EMAILS")


@pytest.fixture(autouse=True)
def no_sign_in(monkeypatch):
    """Sign-in is off unless a test turns it on (tests/test_auth.py)."""
    for var in AUTH_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    """Tests never reach a real model: no model env, and the process-wide client reset."""
    for var in (*ROLE_ENV.values(), "TYPESAFE_API_KEY", "SOPOPS_JEV", "SOPOPS_DECISIONS", "SOPOPS_CHECK_REVIEW", "SOPOPS_CHECK_MODE"):
        monkeypatch.delenv(var, raising=False)
    set_ai(None)
    yield
    set_ai(None)


@pytest.fixture
def files():
    return read_files(FIXTURE / "sops")


@functools.cache
def worker_db(url: str) -> str:
    """Under pytest-xdist each worker gets its own Postgres database (`<name>_gw0`, ...), created on first use."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return url
    base = make_url(url)
    name = f"{base.database}_{worker}"
    engine = engine_from_url(url).execution_options(isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # Two CREATE DATABASEs at once both copy template1 and one fails, so workers take turns.
        conn.execute(text("SELECT pg_advisory_lock(7241)"))
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        conn.execute(text("SELECT pg_advisory_unlock(7241)"))
    engine.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


def fresh_db_url(tmp_path: Path, name: str = "test.db") -> str:
    """SQLite by default; with SOPOPS_TEST_DATABASE_URL, that Postgres database with its tables dropped first."""
    url = os.environ.get("SOPOPS_TEST_DATABASE_URL")
    if url:
        url = worker_db(url)
        engine = engine_from_url(url)
        metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()
        return url
    return f"sqlite:///{tmp_path / name}"


def downgrade(engine, revision: str) -> None:
    """Take a database back to `revision` (Alembic's downgrades), as if it predates the migrations after it."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, revision)


@pytest.fixture
def client(tmp_path):
    """SQLite by default; set SOPOPS_TEST_DATABASE_URL to run against Postgres (its tables are dropped first)."""
    return TestClient(create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))))
