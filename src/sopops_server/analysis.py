"""Pausing a workspace's analysis: sessions keep coming in, and nothing is checked until it's resumed.

While paused, ingest, streaming and the live recorders work as always, and `process_session` normalizes each session
(its transcript shows in the feed) but stops before routing: the session stays `received` (or `incomplete`) with the
reason `PAUSED`, and no model is called for it. Runs already past routing when it's paused finish. The daily recluster
skips the workspace, and reclustering, suggestions and calibration on request answer 409.

Resuming queues `process_session` again for every session held while it was paused. Owners and admins pause and
resume (`auth.access`).

Skipping tags: a session with any of the workspace's `skip_tags` (its own tags, copied from its agent when it came in)
stops before routing too, `excluded` with a reason that names the tag: those calls come in and show, but no model checks
them. Taking a tag off the list queues the sessions it had skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from . import tags as tag_store
from .auth import actor
from .errors import Conflict, NotFound
from .orm import ProcedureCheck, SessionRecord, SessionTag, Workspace
from .pipeline.types import SessionRef

if TYPE_CHECKING:
    from .store import Store

PAUSED = "Analysis is paused."
HELD = ("received", "incomplete")  # where a paused session stops
SKIPPED = "Not checked: tagged "  # + the tag; the reason a skipped session is excluded


def skipped(tag: str) -> str:
    return f"{SKIPPED}{tag}."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


class Analysis:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def state(self, ws: str) -> dict[str, Any]:
        with self._db() as db:
            w = db.get(Workspace, ws)
        at = _utc(w.analysis_paused_at) if w else None
        return {"paused": at is not None, "paused_at": at, "paused_by": w.analysis_paused_by if w and at else None, "skip_tags": list(w.analysis_skip_tags or []) if w else []}

    def skip_tags(self, ws: str) -> list[str]:
        return self.state(ws)["skip_tags"]

    def skip_for(self, ws: str, session_id: str) -> str | None:
        """The first of the workspace's skip tags the session has, or None when it's checked."""
        skip = self.skip_tags(ws)
        if not skip:
            return None
        with self._db() as db:
            have = set(tag_store.for_sessions(db, [session_id])[session_id])
        return next((t for t in skip if t in have), None)

    def set_skip_tags(self, ws: str, tags: list[str]) -> tuple[dict[str, Any], list[SessionRef]]:
        """Set the tags whose sessions aren't checked, and return the sessions skipped for a tag no longer listed, for
        the caller to queue. Sessions that come in with a newly listed tag are skipped; ones already checked keep their
        results."""
        tags = tag_store.clean(tags)
        with self._db() as db, db.begin():
            w = db.get(Workspace, ws)
            if w is None:
                raise NotFound(f"no workspace '{ws}'")
            w.analysis_skip_tags = tags
            db.add(w)
            rows = db.exec(
                select(SessionRecord.id, SessionRecord.payload_hash).where(
                    SessionRecord.workspace_id == ws, SessionRecord.status == "excluded", SessionRecord.status_reason.startswith(SKIPPED),
                ).order_by(SessionRecord.started_at)
            ).all()
            still = set(db.exec(select(SessionTag.session_id).where(SessionTag.workspace_id == ws, SessionTag.tag.in_(tags)))) if tags else set()
            freed = [SessionRef(workspace_id=ws, session_id=sid, revision=rev) for sid, rev in rows if sid not in still]
        return self.state(ws), freed

    def paused(self, ws: str) -> bool:
        return self.state(ws)["paused"]

    def require_running(self, ws: str) -> None:
        """Conflict (409) while the workspace's analysis is paused."""
        if self.paused(ws):
            raise Conflict(f"{PAUSED} An owner or admin resumes it in Settings → Checks.")

    def pause(self, ws: str, by: str | None = None) -> dict[str, Any]:
        """Pause it (already paused: unchanged). `by` is who, unless someone is signed in."""
        a = actor.get()
        with self._db() as db, db.begin():
            w = db.get(Workspace, ws)
            if w is None:
                raise NotFound(f"no workspace '{ws}'")
            if w.analysis_paused_at is None:
                w.analysis_paused_at, w.analysis_paused_by = _now(), (a.name or a.email) if a else by
                db.add(w)
        return self.state(ws)

    def resume(self, ws: str) -> tuple[dict[str, Any], list[SessionRef]]:
        """Resume it, and return the sessions held while it was paused, for the caller to queue."""
        with self._db() as db, db.begin():
            w = db.get(Workspace, ws)
            if w is None:
                raise NotFound(f"no workspace '{ws}'")
            since = w.analysis_paused_at
            w.analysis_paused_at = w.analysis_paused_by = None
            db.add(w)
            held: list[SessionRef] = []
            if since is not None:
                checked = select(ProcedureCheck.session_id).where(ProcedureCheck.workspace_id == ws)
                rows = db.exec(
                    select(SessionRecord.id, SessionRecord.payload_hash).where(
                        SessionRecord.workspace_id == ws, SessionRecord.status.in_(HELD), SessionRecord.processed_at >= since,
                        SessionRecord.id.not_in(checked),
                    ).order_by(SessionRecord.started_at)
                )
                held = [SessionRef(workspace_id=ws, session_id=sid, revision=rev) for sid, rev in rows]
        return self.state(ws), held
