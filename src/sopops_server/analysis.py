"""Pausing a workspace's analysis: sessions keep coming in, and nothing is checked until it's resumed.

While paused, ingest, streaming and the live recorders work as always, and `process_session` normalizes each session
(its transcript shows in the feed) but stops before routing: the session stays `received` (or `incomplete`) with the
reason `PAUSED`, and no model is called for it. Runs already past routing when it's paused finish. The daily recluster
skips the workspace, and reclustering, suggestions and calibration on request answer 409.

Resuming queues `process_session` again for every session held while it was paused. Owners and admins pause and
resume (`auth.access`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from .auth import actor
from .errors import Conflict, NotFound
from .orm import ProcedureCheck, SessionRecord, Workspace
from .pipeline.types import SessionRef

if TYPE_CHECKING:
    from .store import Store

PAUSED = "Analysis is paused."
HELD = ("received", "incomplete")  # where a paused session stops


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
        return {"paused": at is not None, "paused_at": at, "paused_by": w.analysis_paused_by if w and at else None}

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
