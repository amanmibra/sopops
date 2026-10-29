"""The GitHub App: its JWT and installation tokens, installing it, creating one (the manifest flow), webhooks, and two-way
sync between drafts and the repository. GitHub is the in-memory fake of test_github, plus the App's endpoints."""

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from conftest import WS
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sopops_server import github, github_app, repo_sync
from sopops_server.onboarding import kickoff
from sopops_server.orm import FileVersion, GitHubApp, ImportRecord, RepoConnection, Suggestion
from test_github import FakeGitHub, connect, start_repo_onboarding, store_of

APP_ID = "123456"
INSTALLATION = 42
SECRET = "whsec_test_0123456789"
SLUG = "sopops-test"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
PUBLIC = _KEY.public_key()


class AppGitHub(FakeGitHub):
    """FakeGitHub with the App's endpoints: installation tokens (minted with the JWT), the installation, its
    repositories, and the manifest conversion."""

    def __init__(self, files: dict[str, str]):
        super().__init__(files)
        self.minted = 0
        self.expires_in = timedelta(hours=1)
        self.conversion: dict | None = None
        self.token = None  # no installation token minted yet

    def install(self, router: respx.MockRouter) -> None:
        router.route(host="api.github.com").mock(side_effect=self.route)

    def _jwt(self, request: httpx.Request) -> dict:
        auth = request.headers.get("authorization", "")
        assert auth.startswith("Bearer ")
        claims = jwt.decode(auth[7:], PUBLIC, algorithms=["RS256"])
        assert claims["iss"] == APP_ID and claims["exp"] - claims["iat"] <= 600
        return claims

    def route(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        if method == "POST" and path == f"/app/installations/{INSTALLATION}/access_tokens":
            self._jwt(request)
            self.calls.append((method, path))
            self.minted += 1
            self.token = f"ghs_installation_{self.minted}_abcdef0123"
            expires = datetime.now(timezone.utc) + self.expires_in
            return httpx.Response(201, json={"token": self.token, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")})
        if method == "GET" and (m := re.fullmatch(r"/app/installations/(\d+)", path)):
            self._jwt(request)
            if int(m.group(1)) != INSTALLATION:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": INSTALLATION, "account": {"login": "acme"}, "suspended_at": None})
        if method == "GET" and path == "/installation/repositories":
            assert request.headers["authorization"] == f"Bearer {self.token}"
            return httpx.Response(200, json={"total_count": 1, "repositories": [
                {"name": "agents", "full_name": "acme/agents", "owner": {"login": "acme"}, "default_branch": "main", "private": True}]})
        if method == "POST" and (m := re.fullmatch(r"/app-manifests/(\w+)/conversions", path)):
            assert "authorization" not in request.headers
            if self.conversion is None or m.group(1) != "goodcode":
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(201, json=self.conversion)
        return self.handle(request)


@pytest.fixture(autouse=True)
def app_env(monkeypatch):
    for k, v in {"SOPOPS_GITHUB_APP_ID": APP_ID, "SOPOPS_GITHUB_APP_PRIVATE_KEY": PEM, "SOPOPS_GITHUB_WEBHOOK_SECRET": SECRET,
                 "SOPOPS_GITHUB_APP_SLUG": SLUG}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SOPOPS_GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("SOPOPS_GITHUB_API_URL", raising=False)
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    github_app.forget_tokens()
    yield
    github_app.forget_tokens()


@pytest.fixture
def gh(files):
    fake = AppGitHub({**{f"sops/{p}": c for p, c in files.items()}, "README.md": "# agents\n"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


@pytest.fixture
def bare():
    fake = AppGitHub({"README.md": "# agents\n"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def install(client, return_to="/w/demo/settings"):
    url = client.get(f"{WS}/github/install", params={"return_to": return_to}).json()["url"]
    assert url.startswith(f"https://github.com/apps/{SLUG}/installations/new?state=")
    state = parse_qs(urlparse(url).query)["state"][0]
    res = client.get("/v1/github/setup", params={"installation_id": INSTALLATION, "setup_action": "install", "state": state}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == f"{return_to}?github_installation={INSTALLATION}"
    return state


def connect_app(client, **kw):
    install(client)
    res = client.post(f"{WS}/repo", json={"owner": "acme", "repo": "agents", "installation_id": INSTALLATION, **kw})
    assert res.status_code == 200, res.text
    return res.json()


def hook(client, event, payload, secret=SECRET, delivery=None, signature=None):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery or str(uuid.uuid4()), "Content-Type": "application/json"}
    if signature is not False:
        headers["X-Hub-Signature-256"] = signature or github_app.sign(secret, body)
    return client.post("/v1/github/webhook", content=body, headers=headers)


def push(client, fake, changes, before=None, branch="main"):
    """A commit on GitHub, and its push webhook."""
    before = before or fake.refs[branch]
    after = fake.push(branch, changes)
    paths = sorted(changes)
    commit = {"added": [], "modified": [p for p in paths if changes[p] is not None], "removed": [p for p in paths if changes[p] is None]}
    res = hook(client, "push", {"ref": f"refs/heads/{branch}", "before": before, "after": after, "commits": [commit],
                                "repository": {"full_name": "acme/agents"}, "installation": {"id": INSTALLATION}})
    assert res.status_code == 200, res.text
    return after


def head(client):
    return {f["path"]: f["content"] for f in client.get(f"{WS}/files", params={"content": True}).json()["files"]}


def versions(client) -> int:
    with Session(store_of(client).engine) as db:
        return len(list(db.exec(select(FileVersion).where(FileVersion.workspace == "demo"))))


# --- signing in ---------------------------------------------------------------------------------------


def test_the_jwt_and_installation_tokens_are_cached(client, gh, caplog):
    store = store_of(client)
    cfg = github_app.config(store)
    assert cfg.app_id == APP_ID and cfg.source == "env" and PEM not in repr(cfg)
    claims = jwt.decode(github_app.app_jwt(cfg, now=1_700_000_000), PUBLIC, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iss": APP_ID, "iat": 1_700_000_000 - 60, "exp": 1_700_000_000 + 540}

    first = github_app.installation_token(store, INSTALLATION)
    assert github_app.installation_token(store, INSTALLATION) == first and gh.minted == 1  # cached
    github_app.forget_tokens()
    gh.expires_in = timedelta(minutes=4)  # within five minutes of expiring: minted again on the next call
    soon = github_app.installation_token(store, INSTALLATION)
    assert soon != first and gh.minted == 2
    assert github_app.installation_token(store, INSTALLATION) != soon and gh.minted == 3

    connect_app(client)
    out = client.get(f"{WS}/repo").json()
    assert out["auth"] == "app" and out["installation_id"] == INSTALLATION and out["token_hint"] is None
    assert gh.token not in json.dumps(out) and gh.token not in caplog.text and PEM not in caplog.text
    with Session(store.engine) as db:
        assert db.get(RepoConnection, "demo").ciphertext == ""


def test_a_bad_private_key_says_so(client, monkeypatch):
    monkeypatch.setenv("SOPOPS_GITHUB_APP_PRIVATE_KEY", "not a key")
    with pytest.raises(github.GitHubError, match="isn't a valid PEM") as e:
        github_app.installation_token(store_of(client), INSTALLATION)
    assert e.value.status == 503


# --- installing the App -------------------------------------------------------------------------------


def test_installing_records_the_installation_for_the_workspace(client, gh, files):
    app = client.get("/v1/github/app").json()
    assert app["configured"] and app["source"] == "env" and app["install_url"] == f"https://github.com/apps/{SLUG}/installations/new"
    assert PEM not in json.dumps(app) and SECRET not in json.dumps(app)
    # without the install from this workspace, its installation can't be used
    res = client.post(f"{WS}/repo", json={"owner": "acme", "repo": "agents", "installation_id": INSTALLATION})
    assert res.status_code == 400 and "install the GitHub App from this workspace first" in res.json()["detail"]
    state = install(client)
    assert client.get(f"{WS}/github/installations").json() == [{"installation_id": INSTALLATION, "account": "acme"}]
    assert client.get("/v1/workspaces/other/github/installations").json() == []
    repos = client.get(f"{WS}/github/installations/{INSTALLATION}/repositories").json()
    assert repos == [{"owner": "acme", "repo": "agents", "full_name": "acme/agents", "default_branch": "main", "private": True}]
    assert client.get(f"/v1/workspaces/other/github/installations/{INSTALLATION}/repositories").status_code == 400

    # a tampered or foreign state goes back with an error, and records nothing
    raw, _, mac = state.partition(".")
    res = client.get("/v1/github/setup", params={"installation_id": 7, "state": raw + ".00" + mac[2:]}, follow_redirects=False)
    assert res.status_code == 303 and "github_error=" in res.headers["location"] and res.headers["location"].startswith("/w/demo/settings?")
    res = client.get("/v1/github/setup", params={"installation_id": 7}, follow_redirects=False)
    assert res.headers["location"].startswith("/?github_error=")
    assert client.get(f"{WS}/github/install", params={"return_to": "https://evil.example/"}).status_code == 200
    evil = parse_qs(urlparse(client.get(f"{WS}/github/install", params={"return_to": "//evil.example"}).json()["url"]).query)["state"][0]
    res = client.get("/v1/github/setup", params={"installation_id": INSTALLATION, "state": evil}, follow_redirects=False)
    assert res.headers["location"].startswith("/?github_installation=")  # never another site

    connect_app(client)
    assert client.post(f"{WS}/repo/sync").json()["unchanged"] is False
    assert head(client) == files


def test_no_app_shows_the_token_form(client, monkeypatch):
    for k in ("SOPOPS_GITHUB_APP_ID", "SOPOPS_GITHUB_APP_PRIVATE_KEY", "SOPOPS_GITHUB_WEBHOOK_SECRET", "SOPOPS_GITHUB_APP_SLUG"):
        monkeypatch.delenv(k)
    app = client.get("/v1/github/app").json()
    assert app["configured"] is False and app["can_create"] is True
    assert client.get(f"{WS}/github/install").status_code == 400


# --- creating an App (manifest flow) --------------------------------------------------------------------


def test_the_manifest_flow_creates_and_stores_the_app(client, monkeypatch):
    for k in ("SOPOPS_GITHUB_APP_ID", "SOPOPS_GITHUB_APP_PRIVATE_KEY", "SOPOPS_GITHUB_WEBHOOK_SECRET", "SOPOPS_GITHUB_APP_SLUG"):
        monkeypatch.delenv(k)
    fake = AppGitHub({"README.md": "# agents\n"})
    got = client.post("/v1/github/app/manifest", json={"public_url": "https://sopops.example.com", "return_to": "/w/demo/settings",
                                                       "organization": "acme"}).json()
    assert got["action"].startswith("https://github.com/organizations/acme/settings/apps/new?state=")
    manifest = json.loads(got["manifest"])
    assert manifest["default_permissions"] == {"contents": "write", "pull_requests": "write", "metadata": "read"}
    assert manifest["default_events"] == ["push", "pull_request"] and manifest["public"] is False
    assert manifest["hook_attributes"] == {"url": "https://sopops.example.com/v1/github/webhook", "active": True}
    assert manifest["redirect_url"] == "https://sopops.example.com/v1/github/app/manifest/callback"
    assert manifest["setup_url"] == "https://sopops.example.com/v1/github/setup"
    smee = client.post("/v1/github/app/manifest", json={"public_url": "http://127.0.0.1:8484", "webhook_url": "https://smee.io/abc"}).json()
    assert json.loads(smee["manifest"])["hook_attributes"]["url"] == "https://smee.io/abc"
    assert smee["action"].startswith("https://github.com/settings/apps/new?state=")
    state = parse_qs(urlparse(got["action"]).query)["state"][0]

    fake.conversion = {"id": 777, "slug": "sopops-acme", "name": "sopops acme", "owner": {"login": "acme"}, "html_url": "https://github.com/apps/sopops-acme",
                       "pem": PEM, "webhook_secret": "created_secret_0123", "client_id": "Iv1.x", "client_secret": "dont-store-me"}
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        bad = client.get("/v1/github/app/manifest/callback", params={"code": "goodcode", "state": "nope.nope"}, follow_redirects=False)
        assert bad.status_code == 303 and "github_error=" in bad.headers["location"]
        res = client.get("/v1/github/app/manifest/callback", params={"code": "goodcode", "state": state}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/w/demo/settings?github_app=created"
    with Session(store_of(client).engine) as db:
        row = db.get(GitHubApp, "default")
    assert row.app_id == "777" and row.slug == "sopops-acme" and PEM not in row.key_ciphertext and "created_secret" not in row.secret_ciphertext
    assert "dont-store-me" not in json.dumps(row.model_dump(), default=str)
    app = client.get("/v1/github/app").json()
    assert app["configured"] and app["source"] == "db" and app["slug"] == "sopops-acme" and app["can_create"] is False
    assert "created_secret" not in json.dumps(app)
    # its webhook secret verifies webhooks now; another App can't be created over it
    assert hook(client, "ping", {"zen": "hi"}, secret="created_secret_0123").status_code == 200
    assert hook(client, "ping", {"zen": "hi"}).status_code == 401
    assert client.post("/v1/github/app/manifest", json={"public_url": "https://sopops.example.com"}).status_code == 409


# --- webhooks ------------------------------------------------------------------------------------------


def test_webhooks_are_verified_and_deduplicated(client, gh, files, monkeypatch):
    connect_app(client)
    client.post(f"{WS}/repo/sync")
    payload = {"zen": "Keep it logically awesome.", "repository": {"full_name": "acme/agents"}}
    assert hook(client, "ping", payload, signature=False).status_code == 401  # unsigned
    assert hook(client, "ping", payload, signature="sha256=" + "0" * 64).status_code == 401
    assert hook(client, "ping", payload, secret="another secret").status_code == 401
    assert hook(client, "ping", payload).json() == {"ok": True, "event": "ping"}

    voice = files["instructions/brand-voice.md"] + "Smile.\n"
    after = gh.push("main", {"sops/instructions/brand-voice.md": voice})
    body = {"ref": "refs/heads/main", "after": after, "commits": [{"modified": ["sops/instructions/brand-voice.md"]}],
            "repository": {"full_name": "acme/agents"}, "installation": {"id": INSTALLATION}}
    first = hook(client, "push", body, delivery="delivery-1")
    assert first.json() == {"ok": True, "event": "push", "duplicate": False}
    n = versions(client)
    again = hook(client, "push", body, delivery="delivery-1")
    assert again.status_code == 200 and again.json()["duplicate"] is True and versions(client) == n
    assert client.get(f"{WS}/repo").json()["webhook_at"] is not None

    monkeypatch.delenv("SOPOPS_GITHUB_WEBHOOK_SECRET")
    monkeypatch.delenv("SOPOPS_GITHUB_APP_ID")
    assert hook(client, "ping", payload).status_code == 503  # no secret: nothing is accepted


# --- sync: GitHub to sopops ---------------------------------------------------------------------------------


RES = "procedures/reservations.yaml"


def test_a_push_syncs_the_folder_clean_merged_and_conflicting(client, gh, files):
    connect_app(client)
    # Never synced: a push doesn't touch the drafts until the first Sync now.
    push(client, gh, {"sops/instructions/closing.md": "Say goodbye.\n"})
    assert head(client) == {}
    first = client.post(f"{WS}/repo/sync").json()
    assert not first["unchanged"] and first["conflicts"] == [] and head(client)["instructions/closing.md"] == "Say goodbye.\n"
    assert client.get(f"{WS}/repo").json()["pulled_sha"] == gh.refs["main"]

    # clean: changed only on GitHub
    sha = push(client, gh, {"sops/instructions/closing.md": "Say goodbye warmly.\n"})
    assert head(client)["instructions/closing.md"] == "Say goodbye warmly.\n"
    note = client.get(f"{WS}/history/instructions/closing.md").json()[0]
    assert note["note"] == f"Synced from GitHub {sha[:7]}" and note["author"] == "GitHub"
    assert client.get(f"{WS}/repo").json()["pulled_sha"] == sha
    # a push that doesn't touch the folder does nothing
    n = versions(client)
    push(client, gh, {"README.md": "# changed\n"})
    assert versions(client) == n

    # merged: different lines changed here and on GitHub
    text = files[RES]
    mine = text.replace("  - Ask for party size, date and time\n", "  - Ask for party size, date, time and any allergies\n")
    assert client.post(f"{WS}/files", json={"changes": {RES: mine}}).status_code == 200
    theirs = text.replace("  - Never double-book a table\n", "  - Never double-book a table or promise a window seat\n")
    sha = push(client, gh, {f"sops/{RES}": theirs})
    merged = head(client)[RES]
    assert "any allergies" in merged and "window seat" in merged
    assert client.get(f"{WS}/history/{RES}").json()[0]["note"] == f"Synced from GitHub {sha[:7]}; merged with the edits made here to {RES}"

    # conflict: the same line changed both ways. The draft stays; the conflict is listed.
    ours = merged.replace("Confirm the booking details", "Read back the booking details")
    client.post(f"{WS}/files", json={"changes": {RES: ours}})
    theirs_now = gh.files("main")[f"sops/{RES}"].replace("Confirm the booking details", "Double-check the booking details")
    sha = push(client, gh, {f"sops/{RES}": theirs_now, "sops/instructions/closing.md": "Goodbye.\n"})
    assert head(client)[RES] == ours and head(client)["instructions/closing.md"] == "Goodbye.\n"  # the rest still synced
    repo = client.get(f"{WS}/repo").json()
    assert [c["path"] for c in repo["conflicts"]] == [RES] and repo["conflicts"][0]["sha"] == sha and not repo["conflicts"][0]["deleted_on_github"]
    detail = client.get(f"{WS}/repo/conflict", params={"path": RES}).json()
    assert detail["ours"] == ours and detail["theirs"] == theirs_now and "Confirm the booking" in detail["base"]
    # resolving: an edit that doesn't compile is refused and keeps the conflict; GitHub's is taken
    bad = client.post(f"{WS}/repo/conflict", json={"path": RES, "take": "edited", "content": "name: [\n"})
    assert bad.status_code == 422 and client.get(f"{WS}/repo").json()["conflicts"]
    out = client.post(f"{WS}/repo/conflict", json={"path": RES, "take": "github"}).json()
    assert out["conflicts"] == [] and head(client)[RES] == theirs_now
    assert client.post(f"{WS}/repo/conflict", json={"path": RES, "take": "mine"}).status_code == 404


def test_keeping_mine_and_editing_resolve_a_conflict(client, gh, files):
    connect_app(client)
    client.post(f"{WS}/repo/sync")
    client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Mine.\n"}})
    push(client, gh, {"sops/instructions/closing.md": "Theirs.\n"})
    assert [c["path"] for c in client.get(f"{WS}/repo").json()["conflicts"]] == ["instructions/closing.md"]
    assert client.post(f"{WS}/repo/conflict", json={"path": "instructions/closing.md", "take": "mine"}).json()["conflicts"] == []
    assert head(client)["instructions/closing.md"] == "Mine.\n"
    # GitHub unchanged since: the next sync keeps the draft, with no new conflict
    push(client, gh, {"sops/instructions/brand-voice.md": "Be kind.\n"})
    assert head(client)["instructions/closing.md"] == "Mine.\n" and client.get(f"{WS}/repo").json()["conflicts"] == []

    push(client, gh, {"sops/instructions/closing.md": "Theirs again.\n"})
    out = client.post(f"{WS}/repo/conflict", json={"path": "instructions/closing.md", "take": "edited", "content": "Both.\n"}).json()
    assert out["conflicts"] == [] and head(client)["instructions/closing.md"] == "Both.\n"


def test_files_deleted_on_github(client, gh, files):
    connect_app(client)
    push(client, gh, {"sops/instructions/spare.md": "Unused.\n", "sops/instructions/kept.md": "Also unused.\n"})
    client.post(f"{WS}/repo/sync")
    assert {"instructions/spare.md", "instructions/kept.md"} <= set(head(client))
    client.post(f"{WS}/files", json={"changes": {"instructions/kept.md": "Edited here.\n"}})
    push(client, gh, {"sops/instructions/spare.md": None, "sops/instructions/kept.md": None})
    now = head(client)
    assert "instructions/spare.md" not in now  # not edited since the last sync: deleted
    assert now["instructions/kept.md"] == "Edited here.\n"  # edited here: kept, as a conflict
    conflicts = client.get(f"{WS}/repo").json()["conflicts"]
    assert [(c["path"], c["deleted_on_github"]) for c in conflicts] == [("instructions/kept.md", True)]
    client.post(f"{WS}/repo/conflict", json={"path": "instructions/kept.md", "take": "github"})
    assert "instructions/kept.md" not in head(client)


def test_what_does_not_build_is_not_synced(client, gh, files):
    connect_app(client)
    client.post(f"{WS}/repo/sync")
    n = versions(client)
    sha = push(client, gh, {"sops/agents/tonys-pizza.yaml": files["agents/tonys-pizza.yaml"] + "blocks: [no-such-block]\n"})
    repo = client.get(f"{WS}/repo").json()
    assert versions(client) == n and repo["pulled_sha"] != sha and f"GitHub's {sha[:7]} doesn't build" in repo["sync_error"]
    gh.push("main", {"sops/agents/tonys-pizza.yaml": files["agents/tonys-pizza.yaml"]})
    assert client.post(f"{WS}/repo/sync").json()["error"] is None and client.get(f"{WS}/repo").json()["sync_error"] is None


def test_polling_syncs_without_webhooks_and_backs_off_with_them(client, gh, files, monkeypatch):
    connect_app(client)
    client.post(f"{WS}/repo/sync")
    gh.push("main", {"sops/instructions/closing.md": "Polled.\n"})  # no webhook: a server on 127.0.0.1
    store = store_of(client)
    with Session(store.engine) as db, db.begin():
        db.get(RepoConnection, "demo").polled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    client.get(f"{WS}/repo")  # reading the connection polls in the background
    assert head(client)["instructions/closing.md"] == "Polled.\n"

    hook(client, "ping", {"repository": {"full_name": "acme/agents"}})
    hook(client, "pull_request", {"action": "opened", "pull_request": {"html_url": "x"}, "repository": {"full_name": "acme/agents"},
                                  "installation": {"id": INSTALLATION}})
    conn = github.repo_connection(store, "demo")
    assert conn.webhook_at is not None
    with Session(store.engine) as db, db.begin():
        db.get(RepoConnection, "demo").polled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert repo_sync.due(github.repo_connection(store, "demo")) is False  # webhooks arrive: polling backs off
    assert repo_sync.poll(store, "demo")["skipped"] == 1
    assert repo_sync.poll(store, "demo", force=True)["unchanged"] == 1


# --- sync: sopops to GitHub -------------------------------------------------------------------------------


def test_the_sync_pull_request_round_trip_makes_no_new_versions(client, gh, files):
    connect_app(client)
    assert client.post(f"{WS}/repo/send").status_code == 409  # never synced
    client.post(f"{WS}/repo/sync")
    nothing = client.post(f"{WS}/repo/send")
    assert nothing.status_code == 409 and nothing.json()["detail"] == "nothing to send: the drafts match main"

    client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Thanks for calling.\n", "instructions/extra.md": "New.\n"}})
    sent = client.post(f"{WS}/repo/send").json()
    assert sent["url"] == "https://github.com/acme/agents/pull/1" and not sent["updated"]
    assert sent["files"] == ["instructions/closing.md", "instructions/extra.md"]
    pr = gh.pulls[0]
    assert pr["head"] == "sopops/sync" and pr["base"] == "main" and "`sops/instructions/extra.md`: added" in pr["body"]
    assert gh.files("sopops/sync")["sops/instructions/closing.md"] == "Thanks for calling.\n"
    assert client.get(f"{WS}/repo").json()["sync_pr"] == {"url": sent["url"], "state": "open"}

    # another edit: the same pull request, its branch moved to a new commit
    client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Thanks for calling. Goodbye.\n"}})
    again = client.post(f"{WS}/repo/send").json()
    assert again["url"] == sent["url"] and again["updated"] and len(gh.pulls) == 1
    assert gh.files("sopops/sync")["sops/instructions/closing.md"] == "Thanks for calling. Goodbye.\n"

    # merged on GitHub: the pull_request webhook syncs, and the push brings nothing new
    n = versions(client)
    before = gh.refs["main"]
    gh.refs["main"] = gh.commit(gh.files("sopops/sync"), gh.refs["main"])
    gh.pulls[0].update(state="closed", merged=True)
    res = hook(client, "pull_request", {"action": "closed", "pull_request": {"html_url": sent["url"], "merged": True},
                                         "repository": {"full_name": "acme/agents"}, "installation": {"id": INSTALLATION}})
    assert res.status_code == 200
    repo = client.get(f"{WS}/repo").json()
    assert repo["sync_pr"]["state"] == "merged" and repo["pulled_sha"] == gh.refs["main"] and repo["conflicts"] == []
    hook(client, "push", {"ref": "refs/heads/main", "before": before, "after": gh.refs["main"], "commits": [{"modified": ["sops/instructions/closing.md"]}],
                          "repository": {"full_name": "acme/agents"}, "installation": {"id": INSTALLATION}})
    assert versions(client) == n  # identical content: no new draft versions
    assert client.post(f"{WS}/repo/send").status_code == 409  # nothing left to send


def test_sending_syncs_first_and_waits_for_conflicts(client, gh, files):
    connect_app(client)
    client.post(f"{WS}/repo/sync")
    client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Mine.\n"}})
    gh.push("main", {"sops/instructions/closing.md": "Theirs.\n"})
    res = client.post(f"{WS}/repo/send")
    assert res.status_code == 409 and "resolve the 1 sync conflict first" in res.json()["detail"].lower()
    assert gh.pulls == []
    client.post(f"{WS}/repo/conflict", json={"path": "instructions/closing.md", "take": "mine"})
    assert client.post(f"{WS}/repo/send").json()["files"] == ["instructions/closing.md"]


# --- pull requests closed: suggestions and onboarding -------------------------------------------------------


def test_a_merged_pull_request_webhook_updates_suggestions_and_the_onboarding_pull_request(client, bare, files):
    connect_app(client, require_project=False)
    store = store_of(client)
    now = datetime.now(timezone.utc)
    with Session(store.engine) as db, db.begin():
        for n, url in ((1, "https://github.com/acme/agents/pull/1"), (2, "https://github.com/acme/agents/pull/2")):
            db.add(Suggestion(id=f"s{n}", workspace_id="demo", pattern_id="p", procedure_id="reservations", rule_id="r", status="pr_open",
                              pr_url=url, pr_checked_at=now, created_at=now, updated_at=now))
    started = start_repo_onboarding(client)
    id, auth = started["import"]["id"], {"Authorization": f"Bearer {started['token']}"}
    bare.pulls += [{"number": n, "state": "open", "merged": False} for n in (1, 2, 3)]
    client.post("/v1/onboarding/pull-request", headers=auth, json={"url": "https://github.com/acme/agents/pull/3"})
    bare.calls.clear()

    def closed(n, merged):
        return hook(client, "pull_request", {"action": "closed", "pull_request": {"html_url": f"https://github.com/acme/agents/pull/{n}", "merged": merged},
                                             "repository": {"full_name": "acme/agents"}, "installation": {"id": INSTALLATION}})

    closed(1, True)
    closed(2, False)
    with Session(store.engine) as db:
        assert db.get(Suggestion, "s1").status == "merged" and db.get(Suggestion, "s2").status == "dismissed"
    bare.push("main", {f"sops/{p}": c for p, c in files.items()})
    closed(3, True)
    imp = client.get(f"{WS}/imports/{id}").json()
    assert imp["pr_state"] == "merged" and imp["status"] == "applied" and set(head(client)) == set(files)
    assert not [c for c in bare.calls if "/pulls/" in c[1]]  # the webhook said so: GitHub wasn't asked
    assert client.get(f"{WS}/repo").json()["pulled_sha"] == bare.refs["main"]  # synced from now on


def test_sopops_opens_the_onboarding_pull_request_through_the_app(client, bare, files):
    connect_app(client, require_project=False)
    started = start_repo_onboarding(client)
    id, auth = started["import"]["id"], {"Authorization": f"Bearer {started['token']}"}
    session = client.get("/v1/onboarding/session", headers=auth).json()
    assert session["repo_auth"] == "app"
    text = kickoff.instructions(session)
    assert "sopops opens the onboarding pull request itself" in text and "call `open_pull_request`" in text
    res = client.post("/v1/onboarding/pull-request/open", headers=auth, json={"description": "Found in agent.py."})
    assert res.status_code == 400 and "push the proposal first" in res.json()["detail"]
    with Session(store_of(client).engine) as db, db.begin():
        db.get(ImportRecord, id).proposal = {"files": files}
    got = client.post("/v1/onboarding/pull-request/open", headers=auth, json={"description": "Found in agent.py."}).json()
    assert got["pr_url"] == "https://github.com/acme/agents/pull/1" and got["pr_state"] == "open"
    pr = bare.pulls[0]
    assert pr["title"] == "Add sop project for sopops" and pr["body"].startswith("Found in agent.py.")
    assert {p for p in bare.files(pr["head"]) if p.startswith("sops/")} == {f"sops/{p}" for p in files}
    # asking again doesn't open a second one
    assert client.post("/v1/onboarding/pull-request/open", headers=auth, json={}).json()["pr_url"] == got["pr_url"] and len(bare.pulls) == 1


def test_token_connections_keep_the_gh_path(client, bare):
    bare.token = "github_pat_test_0123456789abcdef"
    assert connect(client, require_project=False).status_code == 200
    session = client.get("/v1/onboarding/session", headers={"Authorization": f"Bearer {start_repo_onboarding(client)['token']}"}).json()
    assert session["repo_auth"] == "token" and "gh pr create" in kickoff.instructions(session)


# --- the installation going away --------------------------------------------------------------------------


def test_uninstalling_or_losing_the_repository_breaks_the_connection(client, gh):
    connect_app(client)
    inst = {"id": INSTALLATION, "account": {"login": "acme"}}
    hook(client, "installation", {"action": "suspend", "installation": inst})
    assert "suspended on acme" in client.get(f"{WS}/repo").json()["broken"]
    assert client.post(f"{WS}/repo/sync").status_code == 400
    hook(client, "installation", {"action": "unsuspend", "installation": inst})
    assert client.get(f"{WS}/repo").json()["broken"] is None

    hook(client, "installation_repositories", {"action": "removed", "installation": inst, "repositories_removed": [{"full_name": "acme/agents"}]})
    assert "no longer has access to acme/agents" in client.get(f"{WS}/repo").json()["broken"]
    hook(client, "installation_repositories", {"action": "added", "installation": inst, "repositories_added": [{"full_name": "acme/agents"}]})
    assert client.get(f"{WS}/repo").json()["broken"] is None

    hook(client, "installation", {"action": "deleted", "installation": inst})
    assert "uninstalled from acme" in client.get(f"{WS}/repo").json()["broken"]
    assert client.get(f"{WS}/github/installations").json() == []
    # reconnecting (after installing again) clears it
    connect_app(client)
    assert client.get(f"{WS}/repo").json()["broken"] is None


def test_repo_events_reach_the_live_stream(client, gh):
    connect_app(client)
    store = store_of(client)
    seen: list[dict] = []
    with TestClient(client.app) as live:  # runs the lifespan, which starts the Postgres listener
        listener = getattr(live.app.state, "pg_listener", None)
        if listener:
            assert listener.ready.wait(5)
        stop = store.sessions.broker.subscribe("demo", seen.append)
        try:
            client.post(f"{WS}/repo/sync")
            client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Mine.\n"}})
            push(client, gh, {"sops/instructions/closing.md": "Theirs.\n"})
            deadline = time.time() + 5  # on Postgres the events come back through LISTEN, on another thread
            while len([e for e in seen if e.get("topic") == "repo"]) < 3 and time.time() < deadline:
                time.sleep(0.05)
        finally:
            stop()
    kinds = [e["kind"] for e in seen if e.get("topic") == "repo"]
    assert kinds == ["synced", "synced", "conflict"]
    assert seen[-1]["paths"] == ["instructions/closing.md"]
