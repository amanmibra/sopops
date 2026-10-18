"""GitHub: connecting a repository, pulling its sopc project into drafts, and opening pull requests. GitHub is a small
in-memory git (blobs, flat trees, commits, refs, pulls) behind respx; no network, no real token."""

import base64
import hashlib
import json
import logging
import re

import httpx
import pytest
import respx
from conftest import WS
from cryptography.fernet import Fernet
from sqlmodel import Session

from sopops import github
from sopops.errors import NotFound
from sopops.github import GitHubError
from sopops.orm import RepoConnection
from test_review import PATH, SAFE, env, rule_pattern  # noqa: F401 (env is a fixture)

API = "https://api.github.com"
TOKEN = "github_pat_test_0123456789abcdef"
REPO = "/repos/acme/agents"


def sha1(data) -> str:
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode() if not isinstance(data, bytes) else data).hexdigest()


def blob_sha(content: str) -> str:
    data = content.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    """Enough of GitHub's REST API for sopops: a repo `acme/agents` with branches, git data and pull requests."""

    def __init__(self, files: dict[str, str], branch: str = "main"):
        self.blobs: dict[str, str] = {}
        self.trees: dict[str, dict[str, str]] = {}  # tree sha -> {path: blob sha}
        self.commits: dict[str, dict] = {}
        self.refs: dict[str, str] = {}
        self.pulls: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], httpx.Response] = {}  # (method, path regex) -> response
        self.refs[branch] = self.commit(files)

    # git

    def blob(self, content: str) -> str:
        sha = blob_sha(content)
        self.blobs[sha] = content
        return sha

    def tree(self, entries: dict[str, str]) -> str:
        sha = sha1(entries)
        self.trees[sha] = dict(entries)
        return sha

    def commit(self, files: dict[str, str], parent: str | None = None) -> str:
        tree = self.tree({p: self.blob(c) for p, c in files.items()})
        sha = sha1({"tree": tree, "parent": parent})
        self.commits[sha] = {"tree": tree, "parents": [parent] if parent else []}
        return sha

    def push(self, branch: str, changes: dict[str, str | None]) -> str:
        """A new commit on `branch` with these changes (None deletes)."""
        files = self.files(branch)
        for p, c in changes.items():
            if c is None:
                files.pop(p, None)
            else:
                files[p] = c
        self.refs[branch] = self.commit(files, self.refs[branch])
        return self.refs[branch]

    def files(self, ref: str) -> dict[str, str]:
        commit = self.commits[self.refs.get(ref, ref)]
        return {p: self.blobs[s] for p, s in self.trees[commit["tree"]].items()}

    # HTTP

    def install(self, router: respx.MockRouter) -> None:
        router.route(host="api.github.com").mock(side_effect=self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        for (m, pattern), res in self.fail.items():
            if m == method and re.fullmatch(pattern, path):
                return res
        if not path.startswith(REPO):
            return httpx.Response(404, json={"message": "Not Found"})
        rest = path[len(REPO):]
        body = json.loads(request.content) if request.content else {}
        if method == "GET" and rest == "":
            return httpx.Response(200, json={"full_name": "acme/agents", "default_branch": "main"})
        if method == "GET" and (m := re.fullmatch(r"/branches/(.+)", rest)):
            branch = httpx.URL(path="/" + m.group(1)).path[1:]
            if branch not in self.refs:
                return httpx.Response(404, json={"message": "Branch not found"})
            return httpx.Response(200, json={"name": branch, "commit": {"sha": self.refs[branch]}})
        if method == "GET" and (m := re.fullmatch(r"/contents/(.+)", rest)):
            content = self.files(request.url.params["ref"]).get(m.group(1))
            if content is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"type": "file", "path": m.group(1), "sha": blob_sha(content)})
        if method == "GET" and (m := re.fullmatch(r"/git/commits/(\w+)", rest)):
            return httpx.Response(200, json={"sha": m.group(1), "tree": {"sha": self.commits[m.group(1)]["tree"]}})
        if method == "GET" and (m := re.fullmatch(r"/git/trees/(\w+)", rest)):
            assert request.url.params["recursive"] == "1"
            entries = self.trees[m.group(1)]
            dirs = sorted({"/".join(p.split("/")[:i]) for p in entries for i in range(1, p.count("/") + 1)})
            tree = [{"path": d, "mode": "040000", "type": "tree", "sha": sha1(d)} for d in dirs]
            tree += [{"path": p, "mode": "100644", "type": "blob", "sha": s} for p, s in sorted(entries.items())]
            return httpx.Response(200, json={"sha": m.group(1), "tree": tree, "truncated": False})
        if method == "GET" and (m := re.fullmatch(r"/git/blobs/(\w+)", rest)):
            content = base64.b64encode(self.blobs[m.group(1)].encode()).decode()
            return httpx.Response(200, json={"sha": m.group(1), "content": content, "encoding": "base64"})
        if method == "POST" and rest == "/git/blobs":
            assert body["encoding"] == "utf-8"
            return httpx.Response(201, json={"sha": self.blob(body["content"])})
        if method == "POST" and rest == "/git/trees":
            entries = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if e["sha"] is None:
                    if e["path"] not in entries:
                        return httpx.Response(422, json={"message": "GitHub tree path doesn't exist"})
                    del entries[e["path"]]
                else:
                    entries[e["path"]] = e["sha"]
            return httpx.Response(201, json={"sha": self.tree(entries)})
        if method == "POST" and rest == "/git/commits":
            sha = sha1(body)
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and rest == "/git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[name] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})
        if method == "DELETE" and (m := re.fullmatch(r"/git/refs/heads/(.+)", rest)):
            self.refs.pop(httpx.URL(path="/" + m.group(1)).path[1:])
            return httpx.Response(204)
        if method == "POST" and rest == "/pulls":
            number = len(self.pulls) + 1
            self.pulls.append({**body, "number": number, "state": "open", "merged": False})
            return httpx.Response(201, json={"number": number, "html_url": f"https://github.com/acme/agents/pull/{number}"})
        if method == "GET" and (m := re.fullmatch(r"/pulls/(\d+)", rest)):
            return httpx.Response(200, json=self.pulls[int(m.group(1)) - 1])
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("SOPOPS_GITHUB_API_URL", raising=False)


@pytest.fixture
def gh(files):
    """The repo: the fixture's sopc project at `sops/`, a README at the root, and a committed build/ to ignore."""
    fake = FakeGitHub({**{f"sops/{p}": c for p, c in files.items()}, "README.md": "# agents\n", "sops/build/lock.json": "{}"})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def connect(client, **kw):
    return client.post(f"{WS}/repo", json={"owner": "acme", "repo": "agents", "token": TOKEN, **kw})


def store_of(client):
    return client.app.state.store


# --- connection --------------------------------------------------------------------------------------


def test_connect_checks_the_repo_and_never_returns_the_token(client, gh):
    res = connect(client)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["owner"] == "acme" and out["repo"] == "agents" and out["base_branch"] == "main" and out["path"] == "sops"
    assert out["auth"] == "token" and out["token_hint"] == "…cdef" and out["pulled_sha"] is None
    assert out["url"] == "https://github.com/acme/agents/tree/main/sops"
    assert TOKEN not in res.text and TOKEN not in client.get(f"{WS}/repo").text
    assert client.get(f"{WS}/repo").json() == out
    assert ("GET", f"{REPO}/contents/sops/sopc.yaml") in gh.calls

    with Session(store_of(client).engine) as db:
        row = db.get(RepoConnection, "demo")
    assert TOKEN not in row.ciphertext and github.credentials(row) == TOKEN
    assert github.repo_connection(store_of(client), "demo").owner == "acme"
    assert github.repo_connection(store_of(client), "other") is None


def test_connect_explains_what_is_wrong(client, gh):
    gh.fail[("GET", REPO)] = httpx.Response(401, json={"message": "Bad credentials"})
    res = connect(client)
    assert res.status_code == 400 and "refused the token" in res.json()["detail"]
    del gh.fail[("GET", REPO)]

    res = connect(client, repo="nope")
    assert res.status_code == 400 and "no repository acme/nope" in res.json()["detail"]
    res = connect(client, base_branch="develop")
    assert res.status_code == 400 and "no branch develop" in res.json()["detail"]
    res = connect(client, path="agents")
    assert res.status_code == 400 and "no sopc project at 'agents'" in res.json()["detail"]
    assert connect(client, path="../etc").status_code == 400
    assert connect(client, owner="not an owner").status_code == 400
    assert client.post(f"{WS}/repo", json={"owner": "acme", "repo": "agents"}).json()["detail"] == "enter a GitHub token"

    gh.fail[("GET", REPO)] = httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
    res = connect(client)
    assert res.status_code == 400 and "Contents and Pull requests: read and write" in res.json()["detail"]
    assert client.get(f"{WS}/repo").json() is None  # nothing saved


def test_rate_limits_and_outages(client, gh):
    gh.fail[("GET", REPO)] = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"},
                                            json={"message": "API rate limit exceeded"})
    res = connect(client)
    assert res.status_code == 429 and "resets at 22:13 UTC" in res.json()["detail"]
    gh.fail[("GET", REPO)] = httpx.Response(403, headers={"retry-after": "60"}, json={"message": "You have exceeded a secondary rate limit"})
    assert connect(client).json()["detail"] == "GitHub's rate limit: try again in 60 seconds"
    gh.fail[("GET", REPO)] = httpx.Response(503, text="unavailable")
    assert connect(client).status_code == 502


def test_reconnect_keeps_the_token_and_disconnect_forgets_it(client, gh, files):
    assert connect(client).status_code == 200
    gh.push("main", {f"lib/{p}": c for p, c in files.items()})
    res = client.post(f"{WS}/repo", json={"owner": "acme", "repo": "agents", "path": "/lib/"})
    assert res.status_code == 200 and res.json()["path"] == "lib" and res.json()["token_hint"] == "…cdef"
    assert client.delete(f"{WS}/repo").status_code == 204
    assert client.get(f"{WS}/repo").json() is None  # 200 with null: not connected
    assert client.delete(f"{WS}/repo").status_code == 404
    assert client.post(f"{WS}/repo/pull").status_code == 404


def test_the_token_needs_the_secret_key(client, gh, monkeypatch):
    monkeypatch.delenv("SOPOPS_SECRET_KEY")
    res = connect(client)
    assert res.status_code == 503 and "SOPOPS_SECRET_KEY" in res.json()["detail"]


def test_the_token_is_never_logged(client, gh, caplog):
    caplog.set_level(logging.DEBUG)
    connect(client)
    client.post(f"{WS}/repo/pull")
    gh.fail[("GET", f"{REPO}/branches/main")] = httpx.Response(401, json={"message": f"Bad credentials {TOKEN}"})
    res = client.post(f"{WS}/repo/pull")
    assert res.status_code == 400 and TOKEN not in res.text
    assert TOKEN not in caplog.text


# --- pull --------------------------------------------------------------------------------------------


def test_pull_writes_the_project_as_drafts_and_is_idempotent(client, gh, files):
    assert connect(client).status_code == 200
    res = client.post(f"{WS}/repo/pull", params={"author": "ana"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["sha"] == gh.refs["main"] and out["files"] == len(files) and not out["unchanged"]
    assert sorted(f["path"] for f in out["changed"]) == sorted(files)  # build/ and the README aren't in it
    head = {f["path"]: f["content"] for f in client.get(f"{WS}/files", params={"content": True}).json()["files"]}
    assert head == files
    assert client.get(f"{WS}/draft").json()["agents"]  # drafts, not released
    assert client.get(f"{WS}/releases").json() == []
    assert client.get(f"{WS}/repo").json()["pulled_sha"] == gh.refs["main"]

    gh.calls.clear()
    again = client.post(f"{WS}/repo/pull").json()
    assert again["unchanged"] and again["changed"] == [] and again["sha"] == out["sha"]
    assert gh.calls == [("GET", f"{REPO}/branches/main")]  # the same commit: nothing else is read


def test_pull_reads_only_changed_blobs_and_restores_edited_drafts(client, gh, files):
    connect(client)
    client.post(f"{WS}/repo/pull")
    voice = files["instructions/brand-voice.md"] + "\nAlways smile.\n"
    sha = gh.push("main", {"sops/instructions/brand-voice.md": voice, "README.md": "# changed\n"})
    gh.calls.clear()
    out = client.post(f"{WS}/repo/pull").json()
    assert out["sha"] == sha and [c["path"] for c in out["changed"]] == ["instructions/brand-voice.md"]
    assert [c for c in gh.calls if "/git/blobs/" in c[1]] == [("GET", f"{REPO}/git/blobs/{blob_sha(voice)}")]

    # head edited in sopops: pulling the same commit puts the repository's version back
    assert client.post(f"{WS}/files", json={"changes": {"instructions/closing.md": "Goodbye.\n"}}).status_code == 200
    out = client.post(f"{WS}/repo/pull").json()
    assert not out["unchanged"] and [c["path"] for c in out["changed"]] == ["instructions/closing.md"]
    assert client.get(f"{WS}/files/instructions/closing.md").json()["content"] == files["instructions/closing.md"]


def test_pull_refuses_what_does_not_compile(client, gh, files):
    connect(client)
    gh.push("main", {"sops/agents/tonys-pizza.yaml": files["agents/tonys-pizza.yaml"] + "blocks: [no-such-block]\n"})
    res = client.post(f"{WS}/repo/pull")
    assert res.status_code == 422 and res.json()["valid"] is False
    assert client.get(f"{WS}/repo").json()["pulled_sha"] is None
    gh.push("main", {f"sops/{p}": None for p in files})
    res = client.post(f"{WS}/repo/pull")
    assert res.status_code == 400 and "sopc.yaml isn't there" in res.json()["detail"]


# --- pull requests ---------------------------------------------------------------------------------


def test_open_pull_request_commits_on_a_new_branch(client, gh, files):
    connect(client)
    store = store_of(client)
    base = gh.refs["main"]
    voice = files["instructions/brand-voice.md"] + "\nNever rush the caller.\n"
    url = github.open_pull_request(store, "demo", {"instructions/brand-voice.md": voice, "instructions/closing.md": None},
                                   "Brand voice: don't rush callers", "From pattern review.")
    assert url == "https://github.com/acme/agents/pull/1"
    pr = gh.pulls[0]
    assert pr["base"] == "main" and pr["title"] == "Brand voice: don't rush callers" and pr["body"] == "From pattern review."
    assert re.fullmatch(r"sopops/[0-9a-f]{8}", pr["head"])
    assert gh.refs["main"] == base  # the base branch is untouched
    proposed = gh.files(pr["head"])
    assert proposed["sops/instructions/brand-voice.md"] == voice and "sops/instructions/closing.md" not in proposed
    assert proposed["README.md"] == "# agents\n"
    assert gh.commits[gh.refs[pr["head"]]]["parents"] == [base]

    assert github.pr_status(store, "demo", url) == "open"
    gh.pulls[0].update(state="closed", merged=True)
    assert github.pr_status(store, "demo", url) == "merged"
    gh.pulls[0].update(merged=False)
    assert github.pr_status(store, "demo", url + "/") == "closed"
    with pytest.raises(GitHubError):
        github.pr_status(store, "demo", "https://example.com/not-a-pr")


def test_open_pull_request_refusals(client, gh, files):
    store = store_of(client)
    with pytest.raises(NotFound):
        github.open_pull_request(store, "demo", {"x.md": "x"}, "t", "b")
    connect(client)
    with pytest.raises(GitHubError, match="paths must be relative"):
        github.open_pull_request(store, "demo", {"../x.md": "x"}, "t", "b")
    with pytest.raises(GitHubError) as nothing:
        github.open_pull_request(store, "demo", {"sopc.yaml": files["sopc.yaml"]}, "t", "b")
    assert nothing.value.status == 409 and gh.pulls == []

    gh.fail[("POST", f"{REPO}/pulls")] = httpx.Response(422, json={"message": "Validation Failed", "errors": [{"message": "base is invalid"}]})
    with pytest.raises(GitHubError, match="base is invalid") as failed:
        github.open_pull_request(store, "demo", {"instructions/closing.md": "Bye.\n"}, "t", "b")
    assert failed.value.status == 400
    assert list(gh.refs) == ["main"]  # the branch was deleted again


def test_applying_a_suggestion_opens_a_pull_request(env):
    """The suggestions flow end to end: a verdict, a suggested edit, apply with a repo connected -> a PR, not a draft."""
    for _ in range(3):
        env.send("A")
    never = rule_pattern(env)
    fixed = env.files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}, unless you have just checked the item with lookup_allergens\n")
    env.model.edits = [fixed]
    fake = FakeGitHub({f"sops/{p}": c for p, c in env.files.items()})
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        assert connect(env.client).status_code == 200
        r = env.client.post(f"{WS}/patterns/{never['pattern_id']}/verdict", json={"verdict": "sop_wrong"})
        sid = r.json()["suggestion"]["suggestion_id"]
        applied = env.client.post(f"{WS}/suggestions/{sid}/apply", json={"reviewer": "qa"}).json()
    assert applied["status"] == "pr_open" and applied["pr_url"] == "https://github.com/acme/agents/pull/1"
    pr = fake.pulls[0]
    assert pr["title"].startswith("allergen-check: ") and "Suggested by sopops." in pr["body"]
    assert fake.files(pr["head"])[f"sops/{PATH}"] == fixed
    assert env.client.get(f"{WS}/files/{PATH}").json()["content"] == env.files[PATH]  # no draft edit
