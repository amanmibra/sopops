"""The config file is sop.yaml. What's stored under its old name, sopc.yaml (sop v0.0.8 and earlier, then named sopc), is
renamed by migration 0043; a connected repository that still has it is read as sop.yaml and renamed by the sync pull request."""

import json

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_github import FakeGitHub, connect, secret_key, start_repo_onboarding, store_of  # noqa: F401 (secret_key is a fixture)

from conftest import SOP_BIN, WS, downgrade, fresh_db_url
from sopops_server import Sop, Store, create_app, repo_sync

CFG = f"{WS}/config"
IMPORTS = f"{WS}/imports"


def legacy(files: dict[str, str]) -> dict[str, str]:
    """The fixture project as sop v0.0.8 and earlier (then named sopc) called its config file."""
    return {("sopc.yaml" if p == "sop.yaml" else p): c for p, c in files.items()}


def head(client, ws: str = WS) -> dict[str, str]:
    return {f["path"]: f["content"] for f in client.get(f"{ws}/files", params={"content": True}).json()["files"]}


def test_a_new_workspace_gets_sop_yaml(client):
    assert client.put(f"{CFG}/settings", json={"variables": {"brand": "Acme"}, "procedures_heading": "## Procedures"}).status_code == 200
    assert client.post(f"{CFG}/instructions", json={"id": "hello", "text": "Say hello."}).status_code == 201
    assert set(head(client)) == {"sop.yaml", "instructions/hello.md"}
    assert client.get(CFG).json()["settings"]["file"] == "sop.yaml"


def test_an_uploaded_project_needs_sop_yaml(client, files):
    res = client.post(f"{IMPORTS}/sop", json={"files": legacy(files)})
    assert res.status_code == 400 and "sop.yaml" in res.json()["detail"]
    imp = client.post(f"{IMPORTS}/sop", json={"files": files}).json()
    assert imp["source"] == "sop" and "sop.yaml" in imp["proposal"]["files"]


# --- migration 0043 ------------------------------------------------------------------------------


def _store_as_sopc(db, ws: str) -> None:
    """Rename what `ws` stores from sop.yaml to sopc.yaml, as a sopops before 0043 kept a workspace made with sopc."""
    db.execute(text("UPDATE file_versions SET path = 'sopc.yaml' WHERE workspace = :ws AND path = 'sop.yaml'"), {"ws": ws})
    for number, files, build in db.execute(text("SELECT number, files, build FROM releases WHERE workspace = :ws"), {"ws": ws}).all():
        files = files.replace('"sop.yaml"', '"sopc.yaml"') if isinstance(files, str) else _json(files).replace('"sop.yaml"', '"sopc.yaml"')
        build = build.replace('"sop.yaml"', '"sopc.yaml"') if isinstance(build, str) else _json(build).replace('"sop.yaml"', '"sopc.yaml"')
        db.execute(text("UPDATE releases SET files = :f, build = :b WHERE workspace = :ws AND number = :n"),
                   {"f": files, "b": build, "ws": ws, "n": number})


def _json(value) -> str:
    return json.dumps(value)


def test_the_migration_renames_sopc_yaml_everywhere_without_changing_a_release(tmp_path, files):
    url = fresh_db_url(tmp_path)
    store = Store(url, Sop(SOP_BIN))
    client = TestClient(create_app(store))
    # demo: two releases and a draft; both: a workspace that has both files; an import of a sopc project.
    assert client.put(f"{WS}/files", json={"files": files}).status_code == 200
    assert client.post(f"{WS}/releases", json={"note": "first"}).status_code == 200
    cfg = client.get(CFG).json()
    client.put(f"{CFG}/settings", json={**cfg["settings"], "variables": {**cfg["settings"]["variables"], "brand": "Acme"}})
    assert client.post(f"{WS}/releases", json={"note": "second"}).status_code == 200
    client.post(f"{CFG}/instructions", json={"id": "hours", "text": "We open at noon."})
    prompt = client.get(f"{WS}/agents/tonys-pizza/prompt")
    releases = client.get(f"{WS}/releases").json()
    both = "/v1/workspaces/both"
    assert client.put(f"{both}/files", json={"files": {**files, "sopc.yaml": "version: 1\n"}}).status_code == 200
    imp = client.post(f"{IMPORTS}/sop", json={"files": files}).json()

    downgrade(store.engine, "0042")
    with store.engine.begin() as db:
        _store_as_sopc(db, "demo")
        db.execute(text("INSERT INTO sync_conflicts (workspace, path, sha, base, theirs, created_at, updated_at) "
                        "VALUES ('demo', 'sopc.yaml', 'abc', 'a', 'b', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        proposal = db.execute(text("SELECT proposal FROM imports WHERE id = :id"), {"id": imp["id"]}).scalar()
        proposal = (proposal if isinstance(proposal, str) else _json(proposal)).replace('"sop.yaml"', '"sopc.yaml"')
        db.execute(text("UPDATE imports SET source = 'sopc', proposal = :p WHERE id = :id"), {"p": proposal, "id": imp["id"]})
        assert db.execute(text("SELECT count(*) FROM file_versions WHERE path = 'sopc.yaml'")).scalar() == 3  # demo's 2, both's 1

    client = TestClient(create_app(Store(url, Sop(SOP_BIN))))  # upgrades to head
    assert set(head(client)) == set(files) | {"instructions/hours.md"}
    assert client.get(CFG).json()["settings"]["file"] == "sop.yaml"
    assert [h["file"] for h in client.get(f"{CFG}/settings/history").json()] == ["sop.yaml", "sop.yaml"]
    assert client.get(f"{WS}/releases").json() == releases
    agent = client.get(f"{WS}/agents/tonys-pizza").json()
    assert [c["path"] for c in agent["components"] if c["kind"] == "config"] == ["sop.yaml"]
    got = client.get(f"{WS}/agents/tonys-pizza/prompt")
    assert got.text == prompt.text and got.headers["X-Sop-Hash"] == prompt.headers["X-Sop-Hash"]
    # Only the instruction is a draft: the settings are the same file at the same version as release 2.
    assert [(i["kind"], i["id"]) for i in client.get(f"{WS}/draft").json()["items"]] == [("instruction", "hours")]
    with store.engine.connect() as db:
        assert db.execute(text("SELECT path FROM sync_conflicts")).scalars().all() == ["sop.yaml"]
    imp = client.get(f"{IMPORTS}/{imp['id']}").json()
    assert imp["source"] == "sop" and "sop.yaml" in imp["proposal"]["files"] and "sopc.yaml" not in imp["proposal"]["files"]
    assert {"sop.yaml", "sopc.yaml"} <= set(head(client, both))  # both files: left for its team
    published = client.post(f"{WS}/releases", json={}).json()
    assert published["release"]["number"] == 3  # the instruction; nothing else differs


# --- a connected repository that still has sopc.yaml ---------------------------------------------


@pytest.fixture
def old_repo(files):
    """A connected repo whose project at `sops/` has sopc.yaml."""
    fake = FakeGitHub({**{f"sops/{p}": c for p, c in legacy(files).items()}, "README.md": "# agents\n"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def test_a_repo_with_sopc_yaml_reads_as_sop_yaml_and_the_sync_pull_request_renames_it(client, old_repo, files):
    assert connect(client).status_code == 200, connect(client).text
    found = client.get(f"{WS}/repo/project").json()
    assert found["state"] == "valid" and "tonys-pizza" in found["agents"]
    assert client.post(f"{WS}/repo/pull").status_code == 200
    assert head(client) == files  # sop.yaml, as sopops knows it

    # An edit made in the repo syncs into drafts as sop.yaml: no delete and add.
    changed = legacy(files)["sopc.yaml"].replace("variables:", "variables:\n  brand: Acme", 1)
    old_repo.push("main", {"sops/sopc.yaml": changed})
    out = repo_sync.sync(store_of(client), "demo")
    assert out["error"] is None and [c["path"] for c in out["changed"]] == ["sop.yaml"] and out["conflicts"] == []
    assert client.get(CFG).json()["settings"]["variables"]["brand"] == "Acme" and "sopc.yaml" not in head(client)
    assert client.post(f"{WS}/repo/pull").json()["changed"] == []

    imp = client.post(f"{IMPORTS}/sop", json={"from_repo": True}).json()
    assert imp["status"] == "ready" and "sop.yaml" in imp["proposal"]["files"] and "sopc.yaml" not in imp["proposal"]["files"]

    # Send to GitHub carries the rename, like any other change.
    sent = client.post(f"{WS}/repo/send").json()
    assert sent["files"] == ["sop.yaml", "sopc.yaml"]
    branch = old_repo.files("sopops/sync")
    assert branch["sops/sop.yaml"] == changed and "sops/sopc.yaml" not in branch
    assert "`sops/sop.yaml`: added (renamed from `sopc.yaml`" in old_repo.pulls[0]["body"] and "`sops/sopc.yaml`: deleted" in old_repo.pulls[0]["body"]

    # Merged: the next sync writes nothing, and there's nothing left to send.
    n = len(client.get(f"{CFG}/settings/history").json())
    old_repo.refs["main"] = old_repo.commit(branch, old_repo.refs["main"])
    out = repo_sync.sync(store_of(client), "demo")
    assert out["error"] is None and out["changed"] == [] and len(client.get(f"{CFG}/settings/history").json()) == n
    assert client.post(f"{WS}/repo/send").status_code == 409


def test_an_onboarding_pull_request_replacing_a_broken_old_project_renames_its_config(client, old_repo, files):
    old_repo.push("main", {"sops/agents/tonys-pizza.yaml": files["agents/tonys-pizza.yaml"].replace("  - closing\n", "  - no-such-block\n")})
    connect(client, require_project=False)
    started = start_repo_onboarding(client)
    assert started["import"]["repo_project"] == "invalid"
    auth = {"Authorization": f"Bearer {started['token']}"}
    proposal = client.post(f"{IMPORTS}/sop", json={"files": files}).json()["proposal"]
    content = {k: proposal[k] for k in ("instructions", "procedures", "groups", "agents", "settings")}
    assert client.put("/v1/onboarding/proposal", headers=auth, json={"proposal": content}).status_code == 200
    res = client.post("/v1/onboarding/pull-request/open", headers=auth, json={})
    assert res.status_code == 200, res.text
    branch = old_repo.files(old_repo.pulls[0]["head"])
    assert "sops/sop.yaml" in branch and "sops/sopc.yaml" not in branch

