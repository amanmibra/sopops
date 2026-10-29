"""Two-way sync between a workspace's drafts and the sop project in its GitHub repository.

**GitHub to sopops** (`sync`): on a push to the base branch that touches the folder (the webhook), when polling notices
a new head, and on Sync now. The folder at the new commit ("theirs") is brought into the drafts ("ours") against the
folder at the commit synced last ("base", the connection's `pulled_sha`), file by file:
- the same on both sides, or unchanged on GitHub since the last sync: the draft stays;
- unchanged in the drafts since the last sync: GitHub's version is written (or the draft deleted);
- changed on both sides: a three-way line merge (`sopops_server.merge`). A clean merge is written; a conflict leaves the draft
  alone and is recorded (`sync_conflicts`) until someone keeps theirs, takes GitHub's, or edits it (`resolve`). A file
  deleted on one side and edited on the other is a conflict too.
Everything is one draft write (it must compile with the rest of the drafts, else nothing is written and `sync_error`
says why), noted "Synced from GitHub abc1234". The commit becomes the next base. Writing what a draft already holds
makes no version, so a commit that came from sopops changes nothing. Webhooks and polling only sync a connection synced
before (Sync now, a pull, or the onboarding pull request merging): until then the drafts aren't the repository's.

**sopops to GitHub** (`send`): the drafts' differences from the folder go on one long-lived branch, `sopops/sync`, as
one commit on the base branch's head, with one pull request from it (sending again replaces the commit, so the pull
request shows the drafts as they are). It's followed like the other pull requests (`follow_pr`); when it merges, the
drafts are synced from the base branch, with nothing to write.

Each change goes to the workspace's live stream as an event with `topic: repo` (`synced`, `conflict`, `sync_error`,
`sync_pr`, `pr_merged`, `resolved`, `broken`), so Settings and the import page update without polling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

from sqlmodel import Session, select

from . import forms, github, merge
from .errors import Conflict, NotFound, workspace_id
from .github import GitHubError
from .orm import RepoConnection, SyncConflict
from .runtime import crypto
from .sop import Invalid

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.github")

SYNC_BRANCH = "sopops/sync"
SYNC_TITLE = "Instruction changes from sopops"
AUTHOR = "GitHub"
POLL_EVERY = timedelta(minutes=1)  # a connection's branch is read at most this often by polling (backed off with webhooks)
PR_CHECK_EVERY = timedelta(minutes=1)
TAKES = ("mine", "github", "edited")


@dataclass
class Plan:
    """What a sync writes, and what it leaves for people to decide."""

    changes: dict[str, str | None] = field(default_factory=dict)
    merged: list[str] = field(default_factory=list)
    conflicts: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)  # path -> (base, theirs)
    settled: list[str] = field(default_factory=list)  # the same on both sides now


def reconcile(base: dict[str, str] | None, ours: dict[str, str], theirs: dict[str, str]) -> Plan:
    """Bring `theirs` into `ours` against `base` (None: never synced, so a file on both sides that differs conflicts)."""
    plan = Plan()
    for path in sorted(set(base or {}) | set(ours) | set(theirs)):
        b, o, t = (base or {}).get(path), ours.get(path), theirs.get(path)
        if o == t:
            plan.settled.append(path)
        elif base is not None and t == b:
            continue  # only changed here
        elif base is not None and o == b:
            plan.changes[path] = t  # only changed on GitHub
        elif base is None and o is None:
            plan.changes[path] = t  # new from GitHub
        elif base is None and t is None:
            continue  # only here
        elif b is not None and o is not None and t is not None:
            got = merge.merge(b, o, t)
            if got.clean:
                plan.changes[path] = got.text
                plan.merged.append(path)
            else:
                plan.conflicts[path] = (b, t)
        else:
            plan.conflicts[path] = (b, t)
    return plan


def notify(store: Store, ws: str, kind: str, **data) -> None:
    try:
        store.sessions.notify({"topic": "repo", "workspace": ws, "kind": kind, **data})
    except Exception:  # live updates are a convenience: never fail a sync over one
        log.exception("couldn't publish the %s repo event for %s", kind, ws)


# --- GitHub to sopops ---------------------------------------------------------------------------------


def sync(store: Store, workspace: str, sha: str | None = None, author: str | None = None, force: bool = False) -> dict:
    """Bring the folder at `sha` (default: the base branch's head) into the drafts. A commit already synced is a no-op
    unless `force`. Returns {workspace, sha, changed, merged, conflicts, unchanged, error}."""
    ws = workspace_id(workspace)
    conn = github.require(store, ws)
    if conn.broken:
        raise GitHubError(f"the repository connection is broken: {conn.broken}", 400)
    ours = {f["path"]: f["content"] for f in store.head(ws, content=True)}
    with github.client(store, conn) as gh:
        sha = sha or github.branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        if sha == conn.pulled_sha and not force:
            _save(store, ws, polled_at=_now())
            return _result(ws, sha, unchanged=True)
        theirs = github.read_folder(gh, conn, sha, ours)
        base = None
        if conn.pulled_sha and conn.pulled_sha != sha:
            try:
                base = github.read_folder(gh, conn, conn.pulled_sha, {**ours, **theirs})
            except GitHubError as e:  # the commit is gone (a force push): merge as if never synced
                if e.status != 400:
                    raise
                log.warning("reading %s at the last synced %s failed: %s", ws, conn.pulled_sha[:7], e)
        elif conn.pulled_sha:
            base = theirs
    if forms.config_path(theirs) is None:
        where = f"{conn.path}/" if conn.path else "the repository root"
        message = f"{where} on {conn.base_branch} has no {forms.CONFIG} at {sha[:7]}, so nothing was synced"
        if conn.pulled_sha is None:
            raise GitHubError(message, 400)
        return _failed(store, ws, sha, message)
    plan = reconcile(base, ours, theirs)
    note = f"Synced from GitHub {sha[:7]}" + (f"; merged with the edits made here to {', '.join(plan.merged)}" if plan.merged else "")
    try:
        written = store.edit(ws, plan.changes, author or AUTHOR, note) if plan.changes else []
    except Invalid as e:
        problems = "; ".join(forms.locate(i).text or i.message for i in e.issues if i.severity == "error")[:600]
        return _failed(store, ws, sha, f"GitHub's {sha[:7]} doesn't build with the drafts here, so nothing was synced: {problems}")
    now = _now()
    with Session(store.engine) as db, db.begin():
        row = db.get(RepoConnection, ws)
        if row is not None:
            row.pulled_sha, row.pulled_digest, row.pulled_at, row.polled_at, row.sync_error = sha, github.digest(theirs), now, now, None
        for path in plan.settled:
            if (c := db.get(SyncConflict, (ws, path))) is not None:
                db.delete(c)
        for path, (b, t) in plan.conflicts.items():
            c = db.get(SyncConflict, (ws, path))
            if c is None:
                db.add(SyncConflict(workspace=ws, path=path, sha=sha, base=b, theirs=t, created_at=now, updated_at=now))
            else:
                c.sha, c.base, c.theirs, c.updated_at = sha, b, t, now
    conflicts = sorted(plan.conflicts)
    log.info("synced github %s/%s@%s into %s: %d written, %d merged, %d conflicting", conn.owner, conn.repo, sha[:7], ws, len(written),
             len(plan.merged), len(conflicts))
    notify(store, ws, "synced", sha=sha, changed=len(written), conflicts=conflicts)
    if conflicts:
        notify(store, ws, "conflict", sha=sha, paths=conflicts)
    return {**_result(ws, sha, unchanged=not written), "changed": written, "merged": plan.merged, "conflicts": conflicts}


def _failed(store: Store, ws: str, sha: str, message: str) -> dict:
    _save(store, ws, sync_error=message, polled_at=_now())
    log.warning("syncing %s from %s failed: %s", ws, sha[:7], message)
    notify(store, ws, "sync_error", sha=sha, error=message)
    return {**_result(ws, sha, unchanged=True), "error": message}


def _result(ws: str, sha: str, unchanged: bool) -> dict:
    return {"workspace": ws, "sha": sha, "changed": [], "merged": [], "conflicts": [], "unchanged": unchanged, "error": None}


def conflict(store: Store, workspace: str, path: str) -> dict:
    """A sync conflict, with the three versions: when last synced, the draft here, and GitHub's."""
    ws = workspace_id(workspace)
    with Session(store.engine) as db:
        c = db.get(SyncConflict, (ws, path))
    if c is None:
        raise NotFound(f"no sync conflict for '{path}'")
    ours = next((f["content"] for f in store.head(ws, content=True) if f["path"] == path), None)
    return {"path": c.path, "sha": c.sha, "base": c.base, "ours": ours, "theirs": c.theirs, "at": _utc(c.updated_at)}


def resolve(store: Store, workspace: str, path: str, take: str, content: str | None = None, author: str | None = None) -> dict:
    """Settle a conflict: keep the draft (`mine`, sent with the next Send to GitHub), take GitHub's (`github`), or write
    `content` (`edited`). Raises Invalid if what's written doesn't compile, leaving the conflict."""
    ws = workspace_id(workspace)
    if take not in TAKES:
        raise ValueError(f"take must be one of {', '.join(TAKES)}")
    with Session(store.engine) as db:
        c = db.get(SyncConflict, (ws, path))
    if c is None:
        raise NotFound(f"no sync conflict for '{path}'")
    if take == "github":
        store.edit(ws, {path: c.theirs}, author, f"Took GitHub's version from {c.sha[:7]}")
    elif take == "edited":
        if content is None:
            raise ValueError("send the edited file's content")
        store.edit(ws, {path: content}, author, f"Resolved the sync conflict with GitHub {c.sha[:7]}")
    with Session(store.engine) as db, db.begin():
        if (row := db.get(SyncConflict, (ws, path))) is not None:
            db.delete(row)
    notify(store, ws, "resolved", path=path)
    return github.get(store, ws)


# --- sopops to GitHub ---------------------------------------------------------------------------------


def send(store: Store, workspace: str) -> dict:
    """Open or update the sync pull request with the drafts' differences from the folder at the base branch's head.
    The repository is synced first, so its changes aren't undone; conflicts must be resolved before sending."""
    ws = workspace_id(workspace)
    conn = github.require(store, ws)
    if conn.broken:
        raise GitHubError(f"the repository connection is broken: {conn.broken}", 400)
    if conn.pulled_sha is None:
        raise Conflict("sync from GitHub first, so sopops knows what the repository holds")
    got = sync(store, ws)
    if got["error"]:
        raise Conflict(got["error"])
    with Session(store.engine) as db:
        n = len(list(db.exec(select(SyncConflict.path).where(SyncConflict.workspace == ws))))
    if n:
        raise Conflict(f"resolve the {n} sync conflict{'s' if n != 1 else ''} first")
    conn = github.require(store, ws)
    ours = {f["path"]: f["content"] for f in store.head(ws, content=True)}
    with github.client(store, conn) as gh:
        repo = github.repo_path(conn.owner, conn.repo)
        head = github.branch_head(gh, conn.owner, conn.repo, conn.base_branch)
        files = github.read_folder(gh, conn, head, ours)
        changes = {p: ours.get(p) for p in sorted(set(ours) | set(files)) if ours.get(p) != files.get(p)}
        if not changes:
            raise Conflict(f"nothing to send: the drafts match {conn.base_branch}")
        commit = github.commit_files(gh, conn, head, changes, SYNC_TITLE)
        if commit is None:
            raise Conflict(f"nothing to send: the drafts match {conn.base_branch}")
        if gh.get(f"{repo}/git/ref/heads/{quote(SYNC_BRANCH)}", missing=True, what=f"branch {SYNC_BRANCH}") is not None:
            gh.request("PATCH", f"{repo}/git/refs/heads/{quote(SYNC_BRANCH)}", body={"sha": commit, "force": True}, what=f"branch {SYNC_BRANCH}")
        else:
            gh.post(f"{repo}/git/refs", {"ref": f"refs/heads/{SYNC_BRANCH}", "sha": commit}, what=f"branch {SYNC_BRANCH}")
        url = conn.sync_pr_url if conn.sync_pr_state == "open" else None
        if url is not None and github.pr_status(store, ws, url) != "open":
            url = None
        updated = url is not None
        if url is None:
            url = _open_sync_pr(gh, conn, repo, changes, files)
    _save(store, ws, sync_pr_url=url, sync_pr_state="open", sync_pr_checked_at=None)
    log.info("%s %s with %d file(s) from %s", "updated" if updated else "opened", url, len(changes), ws)
    notify(store, ws, "sync_pr", url=url, state="open")
    return {"url": url, "files": sorted(changes), "updated": updated}


def _open_sync_pr(gh: github.Client, conn: RepoConnection, repo: str, changes: dict[str, str | None], files: dict[str, str]) -> str:
    lines = [f"- `{(f"{conn.path}/{p}" if conn.path else p)}`: {'deleted' if c is None else 'edited' if p in files else 'added'}" for p, c in changes.items()]
    body = ("The drafts in sopops that differ from this branch:\n\n" + "\n".join(lines) +
            "\n\nSending again from sopops updates this pull request. When it merges, sopops syncs its drafts from "
            f"`{conn.base_branch}`.")
    try:
        pr = gh.post(f"{repo}/pulls", {"title": SYNC_TITLE, "body": body, "head": SYNC_BRANCH, "base": conn.base_branch}, what="the sync pull request")
        return pr["html_url"]
    except GitHubError as e:  # one is open already (opened before this connection, or not noticed yet): use it
        if "already exists" not in str(e):
            raise
        found = gh.get(f"{repo}/pulls", params={"head": f"{conn.owner}:{SYNC_BRANCH}", "state": "open"}, what="the sync pull request")
        if not found:
            raise
        return found[0]["html_url"]


def follow_pr(store: Store, workspace: str, state: str | None = None, force: bool = False) -> str | None:
    """The sync pull request's state, applied: `state` as a webhook said, else read from GitHub (at most every
    PR_CHECK_EVERY, backed off while webhooks arrive). Merged syncs the drafts from the base branch."""
    ws = workspace_id(workspace)
    conn = github.repo_connection(store, ws)
    if conn is None or not conn.sync_pr_url or conn.sync_pr_state != "open":
        return conn.sync_pr_state if conn else None
    now = _now()
    if state is None:
        checked = _utc(conn.sync_pr_checked_at)
        if not force and checked is not None and now - checked < github.check_every(conn, PR_CHECK_EVERY):
            return "open"
        try:
            state = github.pr_status(store, ws, conn.sync_pr_url)
        except (GitHubError, crypto.SecretKeyMissing) as e:
            log.warning("checking the sync pull request %s of %s failed: %s", conn.sync_pr_url, ws, e)
            _save(store, ws, sync_pr_checked_at=now)
            return None
    _save(store, ws, sync_pr_state=state, sync_pr_checked_at=now)
    if state == "merged":
        notify(store, ws, "pr_merged", url=conn.sync_pr_url, what="sync")
        try:
            sync(store, ws)
        except (GitHubError, crypto.SecretKeyMissing) as e:
            log.warning("syncing %s after its sync pull request merged failed: %s", ws, e)
    elif state == "closed":
        notify(store, ws, "sync_pr", url=conn.sync_pr_url, state="closed")
    return state


# --- polling: the fallback for webhooks -------------------------------------------------------------------


def due(conn: RepoConnection) -> bool:
    """Whether polling should read this connection's branch now."""
    if conn.broken or conn.pulled_sha is None:
        return False
    at = _utc(conn.polled_at)
    return at is None or _now() - at >= github.check_every(conn, POLL_EVERY)


def poll(store: Store, workspace: str | None = None, force: bool = False) -> dict[str, int]:
    """Sync every connection that's due (of one workspace, or all), and follow open sync pull requests:
    {synced, unchanged, skipped, failed}."""
    with Session(store.engine) as db:
        q = select(RepoConnection)
        conns = list(db.exec(q.where(RepoConnection.workspace == workspace_id(workspace)) if workspace else q))
    out = {"synced": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for conn in conns:
        try:
            follow_pr(store, conn.workspace, force=force)
            if not (due(conn) or (force and not conn.broken and conn.pulled_sha)):
                out["skipped"] += 1
                continue
            got = sync(store, conn.workspace)
            out["failed" if got["error"] else "unchanged" if got["unchanged"] else "synced"] += 1
        except (GitHubError, crypto.SecretKeyMissing, Conflict) as e:
            log.warning("polling %s/%s for %s failed: %s", conn.owner, conn.repo, conn.workspace, e)
            _save(store, conn.workspace, polled_at=_now())
            out["failed"] += 1
    return out


def poll_one(store: Store, workspace: str) -> None:
    """Poll one workspace if it's due: what reading the connection schedules in the background."""
    poll(store, workspace)


# --- internals ---------------------------------------------------------------------------------------


def _save(store: Store, ws: str, **fields) -> None:
    with Session(store.engine) as db, db.begin():
        row = db.get(RepoConnection, ws)
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
