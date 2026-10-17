"""The schema: Alembic migrations, databases from before Alembic, and the UI's catch-all route."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from conftest import SOPC_BIN, WS
from sopops import Sopc, Store, create_app
from sopops.db import current_revision, upgrade
from sopops.models import metadata


def test_migrations_match_the_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade(engine)
    assert current_revision(engine) == "0003"
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []
    upgrade(engine)  # again: nothing to do


def test_a_database_from_before_alembic_is_stamped_and_kept(tmp_path, files):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_engine(url)
    old = [metadata.tables[t] for t in ("workspaces", "file_versions", "releases", "fetches")]
    metadata.create_all(engine, tables=old)  # how sopops made its tables before Alembic
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO workspaces (workspace, current_release) VALUES ('demo', NULL)"))
    assert "alembic_version" not in inspect(engine).get_table_names()

    client = TestClient(create_app(Store(url, Sopc(SOPC_BIN))))
    assert current_revision(engine) == "0003"
    assert client.get("/v1/workspaces").json() == [{"workspace": "demo", "current_release": None}]
    assert client.put(f"{WS}/files", json={"files": files}).status_code == 200


def test_ui_routes_fall_back_to_the_app_and_api_404s_stay_json(client):
    page = client.get("/w/demo/agents/tonys-pizza")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    missing = client.get("/v1/nothing-here")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert client.get("/v1/workspaces").status_code == 200
    assert client.get("/openapi.json").json()["paths"]["/v1/workspaces"]["get"]["operationId"] == "list_workspaces"
