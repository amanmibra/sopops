"""The schema: Alembic migrations, databases from before Alembic, and the UI's catch-all route."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from conftest import SOPC_BIN, WS
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from sopops import Sopc, Store, create_app
from sopops.db import alembic_config, current_revision, upgrade
from sopops.orm import metadata


def test_migrations_match_the_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade(engine)
    assert current_revision(engine) == ScriptDirectory.from_config(alembic_config()).get_current_head()  # one head
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
    assert current_revision(engine) == ScriptDirectory.from_config(alembic_config()).get_current_head()  # one head
    assert client.get("/v1/workspaces").json() == [{"workspace": "demo", "current_release": None}]
    assert client.put(f"{WS}/files", json={"files": files}).status_code == 200


def test_ui_routes_fall_back_to_the_app_and_api_404s_stay_json(client):
    page = client.get("/w/demo/agents/tonys-pizza")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    missing = client.get("/v1/nothing-here")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert client.get("/v1/workspaces").status_code == 200
    assert client.get("/openapi.json").json()["paths"]["/v1/workspaces"]["get"]["operationId"] == "list_workspaces"


def test_ingest_tokens_become_ingest_and_runtime_tokens(tmp_path):
    """0007 moves each workspace's ingest token into `tokens` with scopes [ingest, runtime]; it keeps working."""
    import hashlib

    from alembic import command

    from sopops.db import alembic_config

    url = f"sqlite:///{tmp_path / 'tok.db'}"
    engine = create_engine(url)
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0006")
        conn.execute(text("INSERT INTO workspaces (workspace, current_release) VALUES ('demo', NULL)"))
        conn.execute(
            text("INSERT INTO ingest_tokens (workspace, token_hash, prefix, created_at) VALUES ('demo', :h, 'sopops_abcdef', :at)"),
            {"h": hashlib.sha256(b"sopops_secret").hexdigest(), "at": "2026-10-01 00:00:00.000000"},
        )
    client = TestClient(create_app(Store(url, Sopc(SOPC_BIN))))
    assert "ingest_tokens" not in inspect(engine).get_table_names()
    [tok] = client.get(f"{WS}/tokens").json()
    assert tok["scopes"] == ["ingest", "runtime"] and tok["prefix"] == "sopops_abcdef" and tok["active"]
    ok = client.post(f"{WS}/sessions", json={"platform": "api", "external_id": "x"}, headers={"Authorization": "Bearer sopops_secret"})
    assert ok.status_code == 201
