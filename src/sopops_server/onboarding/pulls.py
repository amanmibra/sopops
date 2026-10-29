"""The onboarding pull request: in repo mode, when the connected folder had no sop project sop could build, the coding
agent opens a pull request adding one and reports its URL (`record`). sopops follows it on GitHub like a suggestion's
(`sync`: the `sopops_pr_status` cron, `sopops pr-status`, and reading the import, at most every `PR_CHECK_EVERY`, backed
off while the repository sends webhooks), and right away when the `pull_request` webhook says it closed (`pr_closed`).
When it merges, the folder is pulled into drafts (`sopops_server.github.pull`) and the import is done: publish the first
release. Closed without merging, it's only noted. With a GitHub App connection, sopops opens the pull request itself
from the pushed proposal (`Onboarding.open_pull_request`); with a token, the coding agent opens it with `gh`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from .. import github, repo_sync
from ..orm import ImportEvent, ImportRecord
from ..sop import Invalid

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops.onboarding")

PR_CHECK_EVERY = timedelta(minutes=1)
DONE = ("applied", "discarded")


def record(store: Store, rec: ImportRecord, url: str, by: str = "agent") -> ImportRecord:
    """The coding agent opened the onboarding pull request: follow it. Raises ValueError for a URL that isn't a pull
    request in the connected repository."""
    url = url.strip()
    m = github.PR_URL.match(url)
    if not m:
        raise ValueError(f"'{url}' isn't a pull request URL (https://github.com/OWNER/REPO/pull/N)")
    if rec.mode != "repo" or not rec.repo:
        raise ValueError("this import isn't in repo mode, so there's no onboarding pull request to follow")
    if f"{m.group(1)}/{m.group(2)}".lower() != rec.repo.lower():
        raise ValueError(f"that pull request is in {m.group(1)}/{m.group(2)}, but this workspace is connected to {rec.repo}")
    with Session(store.engine, expire_on_commit=False) as db:
        r = db.get(ImportRecord, rec.id)
        assert r is not None
        r.pr_url, r.pr_state, r.pr_checked_at, r.pr_merged_at, r.updated_at = url, "open", None, None, _now()
        db.add(r)
        db.add(_event(r.id, "pr", f"Opened the onboarding pull request: {url}. Nothing merges until you merge it.", by))
        db.commit()
        return r


def sync(store: Store, ws: str | None = None, force: bool = False) -> dict[str, int]:
    """Check every open onboarding pull request (of one workspace, or all): {merged, closed, open, failed}."""
    with Session(store.engine) as db:
        q = select(ImportRecord.id).where(ImportRecord.pr_state == "open", ImportRecord.pr_url.is_not(None))
        ids = list(db.exec(q.where(ImportRecord.workspace == ws) if ws else q))
    out = {"merged": 0, "closed": 0, "open": 0, "failed": 0}
    for id in ids:
        out[sync_one(store, id, force) or "failed"] += 1
    return out


def pr_closed(store: Store, workspaces: list[str], url: str, state: str) -> int:
    """A webhook said the pull request at `url` merged or closed: apply it to the imports following it, as polling
    would. Returns how many."""
    with Session(store.engine) as db:
        ids = list(db.exec(select(ImportRecord.id).where(ImportRecord.pr_state == "open", ImportRecord.pr_url == url, ImportRecord.workspace.in_(workspaces))))
    for id in ids:
        sync_one(store, id, force=True, state=state)
    return len(ids)


def sync_one(store: Store, id: str, force: bool = False, state: str | None = None) -> str | None:
    """The pull request's state (`state` when a webhook said it, else read from GitHub), applied to the import; None
    when GitHub couldn't say (logged). Merged pulls the folder into drafts and finishes the import (its token ends)."""
    now = _now()
    with Session(store.engine, expire_on_commit=False) as db:
        r = db.get(ImportRecord, id)
    if r is None or r.pr_state != "open" or not r.pr_url:
        return r.pr_state if r else None
    if state is None:
        checked = _utc(r.pr_checked_at)
        if not force and checked is not None and now - checked < github.check_every(github.repo_connection(store, r.workspace), PR_CHECK_EVERY):
            return "open"
        try:
            state = github.pr_status(store, r.workspace, r.pr_url)
        except github.GitHubError as e:
            log.warning("checking %s for import %s failed: %s", r.pr_url, r.id, e)
            _update(store, r.id, pr_checked_at=now)
            return None
    if state == "open":
        _update(store, r.id, pr_checked_at=now)
    elif state == "closed":
        _update(store, r.id, pr_state="closed", pr_checked_at=now)
        _add(store, r.id, "pr", "The onboarding pull request was closed without merging. Nothing was pulled.")
    else:
        _merged(store, r, now)
    return state


def _merged(store: Store, r: ImportRecord, now: datetime) -> None:
    error = None
    try:
        pulled = github.pull(store, r.workspace, r.created_by)
    except (github.GitHubError, Invalid) as e:
        error = f"The onboarding pull request merged, but pulling {r.repo_path or 'the folder'} failed: {e}"
        log.warning("pulling %s after import %s's pull request merged failed: %s", r.repo, r.id, e)
    with Session(store.engine) as db:
        row = db.get(ImportRecord, r.id)
        assert row is not None
        row.pr_state, row.pr_checked_at, row.pr_merged_at, row.updated_at = "merged", now, now, now
        if error:
            row.error = error
        if not error and row.status not in DONE:  # the merged project is the drafts now
            row.status = "applied"
            store.tokens.revoke_import(db, row.id)
        db.add(row)
        if error:
            db.add(_event(row.id, "merged", error))
        else:
            n = len(pulled["changed"])
            db.add(_event(row.id, "merged", f"Your onboarding PR merged. Pulled {r.repo}@{pulled['sha'][:7]} into drafts "
                          f"({n} file{'s' if n != 1 else ''} changed). Publish your first release."))
        db.commit()
    repo_sync.notify(store, r.workspace, "pr_merged", url=r.pr_url, what="onboarding", import_id=r.id)


def _update(store: Store, id: str, **fields) -> None:
    with Session(store.engine) as db:
        r = db.get(ImportRecord, id)
        if r is not None:
            for k, v in fields.items():
                setattr(r, k, v)
            db.add(r)
            db.commit()


def _add(store: Store, id: str, step: str, message: str) -> None:
    with Session(store.engine) as db:
        db.add(_event(id, step, message))
        db.commit()


def _event(id: str, step: str, message: str, by: str = "sopops") -> ImportEvent:
    return ImportEvent(import_id=id, at=_now(), step=step, message=message[:4000], by=by)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
