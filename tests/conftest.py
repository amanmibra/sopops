import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sopops import Sopc, Store, create_app
from sqlalchemy import text

from sopops.db import engine_from_url
from sopops.models import metadata

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


@pytest.fixture
def files():
    return read_files(FIXTURE / "sops")


@pytest.fixture
def client(tmp_path):
    """SQLite by default; set SOPOPS_TEST_DATABASE_URL to run against Postgres (its tables are dropped first)."""
    url = os.environ.get("SOPOPS_TEST_DATABASE_URL") or os.environ.get("SOPSERVE_TEST_DATABASE_URL")  # old name still read
    if url:
        engine = engine_from_url(url)
        metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    store = Store(url or f"sqlite:///{tmp_path / 'test.db'}", Sopc(SOPC_BIN))
    return TestClient(create_app(store))
