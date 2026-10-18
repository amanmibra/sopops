"""GitHub: a workspace's repository connection, pulling its sopc project into drafts, and pull requests.

A workspace that keeps its instructions in git connects the repository, the branch and the folder of its sopc project
(default `sops` on `main`, where onboarding writes it). **Pull** reads that folder at the branch's head (the git trees and blobs API) and makes it
the workspace's head, as drafts, like `PUT …/files` does; the commit is recorded, so pulling it again is a no-op.
**Pull requests** carry suggestions back: `open_pull_request` commits files to a new `sopops/<id>` branch off the
branch's head (blobs, a tree, a commit, a ref) and opens a PR against it.

Auth is a fine-grained personal access token (Contents and Pull requests: read and write; Metadata: read), encrypted
with SOPOPS_SECRET_KEY like platform keys, and never returned or logged. `credentials` is the one place a connection
turns into a token, so a GitHub App (an installation token minted per call) slots in there as another `auth` kind.
The API base is SOPOPS_GITHUB_API_URL (default https://api.github.com; GitHub Enterprise: https://HOST/api/v3).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from sqlmodel import Session

from . import forms
from .errors import NotFound, workspace_id
from .orm import RepoConnection
from .runtime import crypto
from .sopc import BUILD_DIR, is_safe_path

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.github")

API_ENV = "SOPOPS_GITHUB_API_URL"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
TOKEN = "token"  # the only auth kind for now; a GitHub App would be "app"
DEFAULT_BRANCH = "main"
DEFAULT_PATH = "sops"
CONFIG = "sopc.yaml"
BRANCH_PREFIX = "sopops/"
API_VERSION = "2022-11-28"

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PR_URL = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/pull/(\d+)/?$")


class GitHubError(Exception):
    """A GitHub call or connection problem, in words safe to show (never containing the token). `status` is the
    HTTP status sopops answers with: 400 for something the user fixes (the token, the repo, the path), 409 for a
    conflict, 429 for GitHub's rate limit, 502 when GitHub fails or can't be reached."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def api_url() -> str:
    return os.environ.get(API_ENV, "https://api.github.com").rstrip("/")


# --- the connection ------------------------------------------------------------------------------


def repo_connection(store: Store, workspace_id: str) -> RepoConnection | None:
    """The workspace's connection, or None."""
    with Session(store.engine, expire_on_commit=False) as db:
        return db.get(RepoConnection, _ws(workspace_id))


def credentials(conn: RepoConnection) -> str:
    """The token to call GitHub with. A GitHub App connection would mint an installation token here."""
    if conn.auth == TOKEN:
        return crypto.decrypt(conn.ciphertext)
    raise GitHubError(f"unsupported GitHub auth '{conn.auth}'", 400)


def connection_out(conn: RepoConnection) -> dict:
    """What the API shows of a connection: never the token, only its last characters."""
    return {
        "workspace": conn.workspace,
        "owner": conn.owner,
        "repo": conn.repo,
        "base_branch": conn.base_branch,
        "path": conn.path,
        "auth": conn.auth,
        "token_hint": conn.hint,
        "url": f"https://github.com/{conn.owner}/{conn.repo}/tree/{quote(conn.base_branch)}/{conn.path}".rstrip("/"),
        "pulled_sha": conn.pulled_sha,
        "pulled_at": _utc(conn.pulled_at) if conn.pulled_at else None,
        "created_at": _utc(conn.created_at),
        "updated_at": _utc(conn.updated_at),
    }


def connect(store: Store, workspace: str, owner: str, repo: str, token: str | None = None,
            base_branch: str = DEFAULT_BRANCH, path: str = DEFAULT_PATH, require_project: bool = True) -> dict:
    """Save the workspace's connection, after checking that the token can read the repository and the branch and,
    with `require_project`, that `path` on `base_branch` holds a sopc project (onboarding connects before there is
    one, and proposes it). Without a token, the stored one is kept (to change the branch or path)."""
    ws = _ws(workspace)
    owner, repo, base_branch, path = owner.strip(), repo.strip(), base_branch.strip(), normalize_path(path)
    if not _OWNER.match(owner):
        raise GitHubError(f"'{owner}' isn't a GitHub user or organization name", 400)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _REPO.match(repo) or repo in (".", ".."):
        raise GitHubError(f"'{repo}' isn't a repository name", 400)
    if not base_branch or not _safe_branch(base_branch):
        raise GitHubError(f"'{base_branch}' isn't a branch name", 400)
    existing = repo_connection(store, ws)
    token = (token or "").strip()
    if not token:
        if existing is None:
            raise GitHubError("enter a GitHub token", 400)
        token = credentials(existing)
    with _Client(token) as gh:
        gh.get(_repo_path(owner, repo), what=f"repository {owner}/{repo}")
        head = _branch_head(gh, owner, repo, base_branch)
        if require_project:
            _require_config(gh, owner, repo, head, base_branch, path)
    now = _now()
    with Session(store.engine, expire_on_commit=False) as db, db.begin():
        store.sessions._ensure_workspace(db, ws)
        row = db.get(RepoConnection, ws)
        same = row is not None and (row.owner, row.repo, row.base_branch, row.path) == (owner, repo, base_branch, path)
        if row is None:
            row = RepoConnection(workspace=ws, created_at=now, owner=owner, repo=repo, base_branch=base_branch, path=path, auth=TOKEN,
                                 ciphertext="", hint="", updated_at=now)
            db.add(row)
        row.owner, row.repo, row.base_branch, row.path, row.auth = owner, repo, base_branch, path, TOKEN
        row.ciphertext, row.hint, row.updated_at = crypto.encrypt(token), crypto.hint(token), now
        if not same:  # another project: the next pull isn't a repeat
            row.pulled_sha = row.pulled_digest = row.pulled_at = None
    log.info("connected workspace %s to github %s/%s@%s:%s", ws, owner, repo, base_branch, path or ".")
    return connection_out(row)


def _require_config(gh: _Client, owner: str, repo: str, head: str, base_branch: str, path: str) -> None:
    config = _join(path, CONFIG)
    found = gh.get(f"{_repo_path(owner, repo)}/contents/{quote(config)}", params={"ref": head}, missing=True, what=f"{config} at {base_branch}")
    if found is None or (isinstance(found, dict) and found.get("type") != "file"):
        where = f"'{path}'" if path else "the repository root"
        raise GitHubError(f"no sopc project at {where} on {base_branch}: {config} isn't there", 400)


def disconnect(store: Store, workspace: str) -> None:
    ws = _ws(workspace)
    with Session(store.engine) as db, db.begin():
        row = db.get(RepoConnection, ws)
        if row is None:
            raise NotFound(f"workspace '{ws}' has no repository connected")
        db.delete(row)


def get(store: Store, workspace: str) -> dict:
    conn = repo_connection(store, workspace)
    if conn is None:
        raise NotFound(f"workspace '{workspace}' has no repository connected")
    return connection_out(conn)


def normalize_path(path: str | None) -> str:
    """The project's folder in the repo, without leading or trailing slashes; empty for the root."""
    path = (path or "").strip().strip("/")
    if path in ("", "."):
        return ""
    if not is_safe_path(path):
        raise GitHubError(f"'{path}' isn't a folder in the repository (relative, '/' separated, no '..')", 400)
    return path


# --- pull ------------------------------------------------------------------------------------------


def read_project(store: Store, workspace: str) -> tuple[dict[str, str], str]:
    """The sopc project at the base branch's head, as files (relative to its folder), and the commit, without changing
    the workspace (an import reads it this way, to check before applying)."""
    ws = _ws(workspace)
    conn = _require(store, ws)
    with _Client(credentials(conn)) as gh:
        sha = _branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        files = _read_folder(gh, conn, sha, {})
    if CONFIG not in files:
        where = f"'{conn.path}'" if conn.path else "the repository root"
        raise GitHubError(f"no sopc project at {where} on {conn.base_branch} ({sha[:7]}): {CONFIG} isn't there", 400)
    return files, f"{conn.owner}/{conn.repo}@{sha[:7]}"


def inspect_project(store: Store, workspace: str) -> dict:
    """What the connected folder holds at the base branch's head, compiled with sopc, without changing the workspace:
    `missing` (no folder, or no sopc.yaml in it), `invalid` (sopc can't build it; `issues` says why) or `valid`."""
    ws = _ws(workspace)
    conn = _require(store, ws)
    with _Client(credentials(conn)) as gh:
        sha = _branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        files = _read_folder(gh, conn, sha, {})
    out = {"workspace": ws, "repo": f"{conn.owner}/{conn.repo}", "base_branch": conn.base_branch, "path": conn.path, "sha": sha,
           "files": len(files), "agents": [], "issues": []}
    if CONFIG not in files:
        return {**out, "state": "missing"}
    compiled = store.sopc.compile(files)
    issues = [forms.locate(i).to_dict() for i in compiled.issues]
    if not compiled.valid:
        return {**out, "state": "invalid", "issues": issues}
    return {**out, "state": "valid", "agents": sorted(compiled.agents), "issues": issues}


def pull(store: Store, workspace: str, author: str | None = None) -> dict:
    """Make the workspace's head the sopc project at the base branch's head, as drafts (publish as usual).
    A commit already pulled, with head unchanged since, is a no-op that reads only the branch.
    Raises Invalid (422) if the files don't compile."""
    ws = _ws(workspace)
    conn = _require(store, ws)
    head_files = {f["path"]: f["content"] for f in store.head(ws, content=True)}
    with _Client(credentials(conn)) as gh:
        sha = _branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        if sha == conn.pulled_sha and _digest(head_files) == conn.pulled_digest:
            return {"workspace": ws, "sha": sha, "files": len(head_files), "changed": [], "unchanged": True}
        files = _read_folder(gh, conn, sha, head_files)
    if CONFIG not in files:
        where = f"'{conn.path}'" if conn.path else "the repository root"
        raise GitHubError(f"no sopc project at {where} on {conn.base_branch} ({sha[:7]}): {CONFIG} isn't there", 400)
    changed = store.replace(ws, files, author, f"Pulled from GitHub: {conn.owner}/{conn.repo}@{sha[:7]}")
    with Session(store.engine) as db, db.begin():
        row = db.get(RepoConnection, ws)
        if row is not None:
            row.pulled_sha, row.pulled_digest, row.pulled_at = sha, _digest(files), _now()
    log.info("pulled github %s/%s@%s into %s: %d file(s) changed", conn.owner, conn.repo, sha[:7], ws, len(changed))
    return {"workspace": ws, "sha": sha, "files": len(files), "changed": changed, "unchanged": not changed}


def _read_folder(gh: _Client, conn: RepoConnection, sha: str, known: dict[str, str]) -> dict[str, str]:
    """Every file under the project's folder at commit `sha`, relative to the folder. Files whose blob matches one
    in `known` (head) aren't downloaded again; build/ is skipped (sopc regenerates it)."""
    repo = _repo_path(conn.owner, conn.repo)
    tree_sha = gh.get(f"{repo}/git/commits/{sha}", what=f"commit {sha[:7]}")["tree"]["sha"]
    tree = gh.get(f"{repo}/git/trees/{tree_sha}", params={"recursive": "1"}, what=f"the tree of {sha[:7]}")
    if tree.get("truncated"):
        raise GitHubError("the repository is too large to list in one call; point the connection's path at a smaller folder", 400)
    prefix = conn.path + "/" if conn.path else ""
    by_blob = {_blob_sha(content): content for content in known.values()}
    files: dict[str, str] = {}
    for entry in tree.get("tree", []):
        full = entry.get("path", "")
        if entry.get("type") != "blob" or entry.get("mode") == "120000" or not full.startswith(prefix):
            continue  # folders, submodules, symlinks, and everything outside the project
        rel = full[len(prefix):]
        if rel == BUILD_DIR or rel.startswith(BUILD_DIR + "/"):
            continue
        if entry["sha"] in by_blob:
            files[rel] = by_blob[entry["sha"]]
            continue
        blob = gh.get(f"{repo}/git/blobs/{entry['sha']}", what=full)
        try:
            files[rel] = base64.b64decode(blob.get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise GitHubError(f"{full} isn't UTF-8 text; a sopc project holds only text files", 400) from None
    return files


# --- pull requests -----------------------------------------------------------------------------------


def open_pull_request(store: Store, workspace_id: str, files: Mapping[str, str | None], title: str, body: str) -> str:
    """Commit `files` ({path relative to the project's folder: content, or None to delete}) on a new branch
    `sopops/<id>` off the base branch's head, open a pull request into the base branch, and return its URL.
    Raises NotFound if the workspace has no repository, GitHubError for anything GitHub refuses."""
    ws = _ws(workspace_id)
    conn = _require(store, ws)
    if not files:
        raise GitHubError("no files to propose", 400)
    bad = [p for p in files if not is_safe_path(p)]
    if bad:
        raise GitHubError(f"paths must be relative to the sopc project, use '/', and not contain '..': {', '.join(sorted(bad))}", 400)
    title = title.strip() or "Suggested instruction changes"
    with _Client(credentials(conn)) as gh:
        repo = _repo_path(conn.owner, conn.repo)
        head = _branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        base_tree = gh.get(f"{repo}/git/commits/{head}", what=f"commit {head[:7]}")["tree"]["sha"]
        entries = []
        for path, content in sorted(files.items()):
            blob = None
            if content is not None:
                blob = gh.post(f"{repo}/git/blobs", {"content": content, "encoding": "utf-8"}, what="a file")["sha"]
            entries.append({"path": _join(conn.path, path), "mode": "100644", "type": "blob", "sha": blob})
        tree = gh.post(f"{repo}/git/trees", {"base_tree": base_tree, "tree": entries}, what="the tree")["sha"]
        if tree == base_tree:
            raise GitHubError(f"{conn.base_branch} already has these changes; nothing to propose", 409)
        commit = gh.post(f"{repo}/git/commits", {"message": title, "tree": tree, "parents": [head]}, what="the commit")["sha"]
        branch = BRANCH_PREFIX + uuid.uuid4().hex[:8]
        gh.post(f"{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit}, what=f"branch {branch}")
        try:
            pr = gh.post(f"{repo}/pulls", {"title": title, "body": body, "head": branch, "base": conn.base_branch}, what="the pull request")
        except GitHubError:
            try:  # don't leave a branch behind for a PR that doesn't exist
                gh.request("DELETE", f"{repo}/git/refs/heads/{quote(branch)}", what=f"branch {branch}")
            except GitHubError:
                log.warning("couldn't delete branch %s in %s/%s after the pull request failed", branch, conn.owner, conn.repo)
            raise
    log.info("opened %s from %s in %s", pr.get("html_url"), branch, ws)
    return pr["html_url"]


def pr_status(store: Store, workspace_id: str, url: str) -> str:
    """A pull request's state: `open`, `merged` or `closed` (without merging), read with the workspace's token."""
    m = PR_URL.match(url.strip())
    if not m:
        raise GitHubError(f"'{url}' isn't a pull request URL", 400)
    owner, repo, number = m.groups()
    conn = _require(store, _ws(workspace_id))
    with _Client(credentials(conn)) as gh:
        pr = gh.get(f"/repos/{quote(owner)}/{quote(repo)}/pulls/{number}", what=f"pull request {owner}/{repo}#{number}")
    if pr.get("merged") or pr.get("merged_at"):
        return "merged"
    return "open" if pr.get("state") == "open" else "closed"


# --- calling GitHub ----------------------------------------------------------------------------------


class _Client:
    """GitHub's REST API with one token, turning every failure into a GitHubError without the token in it."""

    def __init__(self, token: str):
        self._token = token
        self._http = httpx.Client(
            base_url=api_url(),
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
                     "User-Agent": "sopops"},
        )

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def get(self, path: str, params: dict | None = None, what: str = "it", missing: bool = False) -> Any:
        return self.request("GET", path, params=params, what=what, missing=missing)

    def post(self, path: str, body: dict, what: str = "it") -> Any:
        return self.request("POST", path, body=body, what=what)

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None, what: str = "it",
                missing: bool = False) -> Any:
        """The response's JSON. `missing`: a 404 returns None instead of raising."""
        try:
            res = self._http.request(method, path, json=body, params=params)
        except httpx.HTTPError as e:
            raise GitHubError(f"couldn't reach GitHub: {type(e).__name__}") from None
        if res.status_code == 404 and missing:
            return None
        if res.status_code >= 400:
            raise _error(res, what, self._token)
        try:
            return res.json() if res.content else {}
        except ValueError:
            raise GitHubError("GitHub returned a response that isn't JSON") from None


def _error(res: httpx.Response, what: str, token: str) -> GitHubError:
    """A failed response in plain words."""
    code = res.status_code
    try:
        payload = res.json()
    except ValueError:
        payload = {}
    message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        message += ": " + "; ".join(e.get("message") or e.get("code") or json.dumps(e) if isinstance(e, dict) else str(e) for e in errors)
    message = message.replace(token, "…")[:300] if token else message[:300]
    if code in (403, 429) and (res.headers.get("x-ratelimit-remaining") == "0" or res.headers.get("retry-after") or "rate limit" in message.lower()):
        if retry := res.headers.get("retry-after"):
            return GitHubError(f"GitHub's rate limit: try again in {retry} seconds", 429)
        reset = res.headers.get("x-ratelimit-reset")
        when = f" at {datetime.fromtimestamp(int(reset), timezone.utc):%H:%M} UTC" if reset and reset.isdigit() else " within the hour"
        return GitHubError(f"GitHub's rate limit for this token is used up; it resets{when}", 429)
    if code == 401:
        return GitHubError("GitHub refused the token (401): it's wrong, expired or revoked", 400)
    if code == 403:
        return GitHubError(f"the token isn't allowed to access {what} (403{': ' + message if message else ''}). A fine-grained token needs "
                           "this repository, with Contents and Pull requests: read and write, and Metadata: read", 400)
    if code == 404:
        return GitHubError(f"GitHub found no {what} (404): check the name, and that the token has access to the repository", 400)
    if code == 409:
        return GitHubError(f"GitHub refused {what} (409{': ' + message if message else ''}); an empty repository has nothing to read", 409)
    if code == 422:
        return GitHubError(f"GitHub rejected {what} (422{': ' + message if message else ''})", 400)
    return GitHubError(f"GitHub answered {code} for {what}{': ' + message if message else ''}")


def _branch_head(gh: _Client, owner: str, repo: str, branch: str) -> str:
    """The commit at the head of `branch`."""
    found = gh.get(f"{_repo_path(owner, repo)}/branches/{quote(branch, safe='')}", what=f"branch {branch} in {owner}/{repo}")
    return found["commit"]["sha"]


def _repo_path(owner: str, repo: str) -> str:
    return f"/repos/{quote(owner)}/{quote(repo)}"


def _require(store: Store, ws: str) -> RepoConnection:
    conn = repo_connection(store, ws)
    if conn is None:
        raise NotFound(f"workspace '{ws}' has no repository connected")
    return conn


def _join(folder: str, path: str) -> str:
    return f"{folder}/{path}" if folder else path


def _safe_branch(name: str) -> bool:
    """Git's ref-name rules, as far as they matter here."""
    return (len(name) <= 255 and ".." not in name and not name.startswith(("/", "-")) and not name.endswith(("/", ".", ".lock"))
            and not re.search(r"[\s~^:?*\[\\\x00-\x1f\x7f]|@\{|//", name))


def _blob_sha(content: str) -> str:
    """Git's id for a blob with this content."""
    data = content.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _ws(workspace: str) -> str:
    return workspace_id(workspace)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    """In UTC: Postgres returns the server's zone, SQLite no zone."""
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
