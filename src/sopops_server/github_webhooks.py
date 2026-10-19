"""What sopops does with GitHub's webhooks (`POST /v1/github/webhook`, after the signature is checked and the delivery
deduplicated; see `sopops_server.api.github`).

- `push` to a connection's base branch that touches its folder: sync the folder into the drafts (`sopops_server.repo_sync`).
- `pull_request` closed: the suggestions, onboarding imports and sync pull request following it are updated now,
  through the same code the pollers run, with the state the webhook gives.
- `installation` deleted or suspended, `installation_repositories` removed: the connections through it are marked
  broken, and Settings says so; unsuspended or added back, they work again.

Connections are found by repository (and, for App connections, installation). Any webhook for a repository marks its
connections `webhook_at`, so the pollers back off while webhooks arrive.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, delete, func, select

from . import github, github_app, repo_sync
from .github import GitHubError
from .onboarding import pulls
from .orm import GitHubInstallation, RepoConnection
from .runtime import crypto

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.github")


def handle(store: Store, event: str, payload: dict[str, Any]) -> dict:
    """Act on one webhook. Returns what was done, for the response and the logs."""
    if event == "push":
        return _push(store, payload)
    if event == "pull_request":
        return _pull_request(store, payload)
    if event == "installation":
        return _installation(store, payload)
    if event == "installation_repositories":
        return _installation_repositories(store, payload)
    return {"event": event, "ignored": True}


def _connections(store: Store, payload: dict[str, Any]) -> list[RepoConnection]:
    """The connections to the payload's repository, marked as hearing from GitHub now."""
    full = ((payload.get("repository") or {}).get("full_name") or "").lower()
    owner, _, name = full.partition("/")
    installation = (payload.get("installation") or {}).get("id")
    if not owner or not name:
        return []
    with Session(store.engine, expire_on_commit=False) as db, db.begin():
        rows = db.exec(select(RepoConnection).where(func.lower(RepoConnection.owner) == owner, func.lower(RepoConnection.repo) == name))
        found = [c for c in rows if c.auth != github.APP or installation is None or c.installation_id == installation]
        for c in found:
            c.webhook_at = _now()
    return found


def _touches(payload: dict[str, Any], folder: str) -> bool:
    """Whether the push changed anything in the folder. Without the commits (a force push back), assume it did."""
    commits = payload.get("commits") or []
    if not folder or not commits:
        return True
    prefix = folder + "/"
    return any(p.startswith(prefix) for c in commits for k in ("added", "modified", "removed") for p in c.get(k) or [])


def _push(store: Store, payload: dict[str, Any]) -> dict:
    synced, skipped = [], []
    for conn in _connections(store, payload):
        if (payload.get("ref") != f"refs/heads/{conn.base_branch}" or payload.get("deleted") or not payload.get("after")
                or not _touches(payload, conn.path) or conn.broken or conn.pulled_sha is None):
            skipped.append(conn.workspace)
            continue
        try:
            got = repo_sync.sync(store, conn.workspace, sha=payload["after"])
            (skipped if got["error"] else synced).append(conn.workspace)
        except (GitHubError, crypto.SecretKeyMissing) as e:
            log.warning("syncing %s after a push to %s/%s failed: %s", conn.workspace, conn.owner, conn.repo, e)
            skipped.append(conn.workspace)
    return {"event": "push", "synced": synced, "skipped": skipped}


def _pull_request(store: Store, payload: dict[str, Any]) -> dict:
    conns = _connections(store, payload)
    pr = payload.get("pull_request") or {}
    if payload.get("action") != "closed" or not conns or not pr.get("html_url"):
        return {"event": "pull_request", "ignored": True}
    url, state = pr["html_url"], "merged" if pr.get("merged") else "closed"
    workspaces = [c.workspace for c in conns]
    out = {"event": "pull_request", "state": state, "suggestions": store.suggestions.pr_closed(workspaces, url, state),
           "imports": pulls.pr_closed(store, workspaces, url, state), "sync": 0}
    for conn in conns:
        if conn.sync_pr_url == url:
            repo_sync.follow_pr(store, conn.workspace, state)
            out["sync"] += 1
    return out


def _installation(store: Store, payload: dict[str, Any]) -> dict:
    action = payload.get("action")
    inst = payload.get("installation") or {}
    account = (inst.get("account") or {}).get("login") or "its account"
    if action == "deleted":
        reason = f"The GitHub App was uninstalled from {account}. Install it again, or connect with a token."
    elif action == "suspend":
        reason = f"The GitHub App is suspended on {account}. Unsuspend it on GitHub, or connect with a token."
    elif action == "unsuspend":
        reason = None
    else:
        return {"event": "installation", "ignored": True}
    if inst.get("id") is None:
        return {"event": "installation", "ignored": True}
    github_app.forget_tokens(inst["id"])
    with Session(store.engine) as db, db.begin():
        conns = list(db.exec(select(RepoConnection).where(RepoConnection.auth == github.APP, RepoConnection.installation_id == inst["id"])))
        for c in conns:
            c.broken = reason
        if action == "deleted":
            db.exec(delete(GitHubInstallation).where(GitHubInstallation.installation_id == inst["id"]))
    for c in conns:
        repo_sync.notify(store, c.workspace, "broken", reason=reason)
    log.info("github installation %s %s: %d connection(s)", inst["id"], action, len(conns))
    return {"event": "installation", "action": action, "connections": [c.workspace for c in conns]}


def _installation_repositories(store: Store, payload: dict[str, Any]) -> dict:
    inst = (payload.get("installation") or {}).get("id")
    if inst is None:
        return {"event": "installation_repositories", "ignored": True}
    removed = {(r.get("full_name") or "").lower() for r in payload.get("repositories_removed") or []}
    added = {(r.get("full_name") or "").lower() for r in payload.get("repositories_added") or []}
    changed = []
    with Session(store.engine) as db, db.begin():
        for c in db.exec(select(RepoConnection).where(RepoConnection.auth == github.APP, RepoConnection.installation_id == inst)):
            full = f"{c.owner}/{c.repo}".lower()
            if full in removed:
                c.broken = f"The GitHub App no longer has access to {c.owner}/{c.repo}. Add the repository to the App's installation on GitHub."
            elif full in added and c.broken:
                c.broken = None
            else:
                continue
            changed.append((c.workspace, c.broken))
    for ws, reason in changed:
        repo_sync.notify(store, ws, "broken", reason=reason)
    return {"event": "installation_repositories", "connections": [ws for ws, _ in changed]}


def _now() -> datetime:
    return datetime.now(timezone.utc)
