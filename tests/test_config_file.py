"""The config file: new projects get sop.yaml; one that has sopc.yaml (the name before sop v0.1.0) keeps it everywhere."""

import pytest
import respx
from test_github import FakeGitHub, connect, secret_key, start_repo_onboarding, store_of  # noqa: F401 (secret_key is a fixture)

from conftest import WS
from sopops_server import Sop, Sopc, repo_sync

CFG = f"{WS}/config"
IMPORTS = f"{WS}/imports"


def legacy(files: dict[str, str]) -> dict[str, str]:
    """The fixture project as a team made it before sop v0.1.0: its config file is sopc.yaml."""
    return {("sopc.yaml" if p == "sop.yaml" else p): c for p, c in files.items()}


def head(client) -> dict[str, str]:
    return {f["path"]: f["content"] for f in client.get(f"{WS}/files", params={"content": True}).json()["files"]}


def test_a_new_workspace_gets_sop_yaml(client):
    assert client.put(f"{CFG}/settings", json={"variables": {"brand": "Acme"}, "procedures_heading": "## Procedures"}).status_code == 200
    assert client.post(f"{CFG}/instructions", json={"id": "hello", "text": "Say hello."}).status_code == 201
    assert set(head(client)) == {"sop.yaml", "instructions/hello.md"}
    assert client.get(CFG).json()["settings"]["file"] == "sop.yaml"


def test_a_workspace_with_only_sopc_yaml_works_end_to_end(client, files):
    old = legacy(files)
    assert client.put(f"{WS}/files", json={"files": old}).status_code == 200
    assert client.post(f"{WS}/releases", json={"note": "first"}).status_code == 200
    cfg = client.get(CFG).json()
    assert cfg["settings"]["file"] == "sopc.yaml" and cfg["settings"]["version"] == 1
    assert {g["file"] for g in cfg["groups"]} == {"sopc.yaml"}

    # Settings, groups, new and deleted items all write sopc.yaml; sop.yaml never appears.
    res = client.put(f"{CFG}/settings", json={**cfg["settings"], "variables": {**cfg["settings"]["variables"], "brand": "Acme"}})
    assert res.status_code == 200, res.json()
    assert [c["path"] for c in res.json()["changed"]] == ["sopc.yaml"]
    assert client.post(f"{CFG}/groups", json={"id": "front", "blocks": ["restaurant-host", "brand-voice"]}).json()["changed"][0]["path"] == "sopc.yaml"
    assert client.post(f"{CFG}/instructions", json={"id": "hours", "text": "We open at noon."}).status_code == 201
    luigi = client.get(f"{CFG}/agents/luigis-trattoria").json()
    assert client.put(f"{CFG}/agents/luigis-trattoria", json={**luigi, "blocks": [*luigi["blocks"], "hours"]}).status_code == 200
    assert client.delete(f"{CFG}/groups/front").status_code == 200
    assert client.delete(f"{CFG}/instructions/hours").status_code == 200
    assert "sop.yaml" not in head(client) and "brand: Acme" in head(client)["sopc.yaml"]
    preview = client.post(f"{CFG}/agents/luigis-trattoria/preview", json=None)
    assert preview.status_code == 200 and preview.json()["valid"]

    # The draft lists the settings change; history, versions and releases read sopc.yaml.
    items = {(i["kind"], i["id"]): i for i in client.get(f"{WS}/draft").json()["items"]}
    assert items[("settings", "settings")]["change"] == "edited"
    assert [h["file"] for h in client.get(f"{CFG}/settings/history").json()] == ["sopc.yaml", "sopc.yaml"]
    assert client.get(f"{CFG}/settings/versions/1").json()["file"] == "sopc.yaml"
    assert client.post(f"{WS}/releases", json={"note": "second"}).status_code == 200
    agent = client.get(f"{WS}/agents/luigis-trattoria").json()
    assert [c["path"] for c in agent["components"] if c["kind"] == "config"] == ["sopc.yaml"]

    # An import into it keeps sopc.yaml, whether a proposal or a project that calls its config sop.yaml.
    imp = client.post(f"{IMPORTS}/sop", json={"files": {**files, "instructions/extra.md": "Be brief.\n"}}).json()
    applied = client.post(f"{IMPORTS}/{imp['id']}/apply", json={}).json()
    assert "sop.yaml" not in {c["path"] for c in applied["changed"]} and "sop.yaml" not in head(client)
    assert client.get(CFG).json()["settings"]["file"] == "sopc.yaml"


def test_sop_yaml_wins_when_both_are_there(client, files):
    both = {**files, "sopc.yaml": "version: 1\nvariables:\n  ignored: x\n"}
    assert client.put(f"{WS}/files", json={"files": both}).status_code == 200
    cfg = client.get(CFG).json()
    assert cfg["settings"]["file"] == "sop.yaml" and "ignored" not in cfg["settings"]["variables"]
    res = client.put(f"{CFG}/settings", json={**cfg["settings"], "procedures_heading": "## How we work"})
    assert [c["path"] for c in res.json()["changed"]] == ["sop.yaml"]


def test_the_old_import_path_still_works(client, files):
    res = client.post(f"{IMPORTS}/sopc", json={"files": legacy(files)})
    assert res.status_code == 201, res.text
    imp = res.json()
    assert imp["source"] == "sopc" and imp["proposal"]["files"] == legacy(files)
    assert client.post(f"{IMPORTS}/{imp['id']}/apply", json={}).status_code == 200
    assert "sopc.yaml" in head(client) and "sop.yaml" not in head(client)  # a new workspace takes the project as it is


@pytest.fixture
def old_repo(files):
    """A connected repo whose project at `sops/` has sopc.yaml."""
    fake = FakeGitHub({**{f"sops/{p}": c for p, c in legacy(files).items()}, "README.md": "# agents\n"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def test_a_repo_with_sopc_yaml_connects_pulls_and_syncs_without_renaming(client, old_repo, files):
    assert connect(client).status_code == 200, connect(client).text  # require_project accepts sopc.yaml
    found = client.get(f"{WS}/repo/project").json()
    assert found["state"] == "valid" and "tonys-pizza" in found["agents"]
    assert client.post(f"{WS}/repo/pull").status_code == 200
    assert set(head(client)) == set(legacy(files))

    # An edit made in the repo syncs into drafts; the file is still sopc.yaml.
    changed = legacy(files)["sopc.yaml"].replace("variables:", "variables:\n  brand: Acme", 1)
    old_repo.push("main", {"sops/sopc.yaml": changed})
    out = repo_sync.sync(store_of(client), "demo")
    assert out["error"] is None and [c["path"] for c in out["changed"]] == ["sopc.yaml"]
    assert client.get(CFG).json()["settings"]["variables"]["brand"] == "Acme"
    assert "sop.yaml" not in head(client) and "sops/sop.yaml" not in old_repo.files("main")

    # An import from the repo reads it too.
    imp = client.post(f"{IMPORTS}/sop", json={"from_repo": True}).json()
    assert imp["status"] == "ready" and "sopc.yaml" in imp["proposal"]["files"]


def test_an_onboarding_pull_request_replacing_a_broken_legacy_project_keeps_sopc_yaml(client, old_repo, files):
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
    assert "sops/sopc.yaml" in branch and "sops/sop.yaml" not in branch


def test_the_binary_is_sop_bin_else_the_old_sopc_bin(monkeypatch):
    monkeypatch.setenv("SOP_BIN", "/new/sop")
    monkeypatch.setenv("SOPC_BIN", "/old/sopc")
    assert Sop().binary == "/new/sop"
    monkeypatch.delenv("SOP_BIN")
    assert Sop().binary == "/old/sopc"
    assert Sopc is Sop  # the old class name, still exported for sopops-hosted
