"""`process_session`: what runs after a session is ingested. M1: normalize the turns and set the status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import ProcessResult, SessionRef
from .normalize import normalize_turns

if TYPE_CHECKING:
    from ..store import Store


def process_session(store: Store, ref: SessionRef) -> ProcessResult:
    from ..store import NotFound

    try:
        session = store.sessions.load(ref.workspace_id, ref.session_id)
    except NotFound:  # deleted since: nothing to do, and nothing to retry
        return ProcessResult(session_id=ref.session_id, status="failed", turn_count=0, skipped=True)
    if session.payload_hash != ref.revision:
        return ProcessResult(session_id=ref.session_id, status=session.status, turn_count=len(session.turns), skipped=True)
    try:
        turns = normalize_turns(session.turns, session.started_at)
    except Exception as e:  # a payload we can't make sense of: keep it, and say why
        store.sessions.finish(ref, session.turns, "failed", f"normalize: {e}")
        raise
    if session.status == "partial":  # set at ingest when the sender says the transcript is incomplete
        status, reason = "partial", session.status_reason
    elif not turns:
        status, reason = "partial", "No turns."
    else:
        status, reason = "received", None
    applied = store.sessions.finish(ref, turns, status, reason)
    return ProcessResult(session_id=ref.session_id, status=status, turn_count=len(turns), skipped=not applied)
