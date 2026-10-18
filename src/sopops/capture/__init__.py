"""Sessions with no code in the agent: sopops records them from the platform.

- LiveKit (`livekit`): a webhook from LiveKit Cloud says an agent joined a room; sopops joins it as a hidden
  participant, reads the transcript the agent publishes, and streams it into a session that closes with the room.
- ElevenLabs (`elevenlabs`): the post-call webhook brings each finished conversation; on Enterprise, the
  conversation-initiation webhook can also start the monitor socket, which streams it while it runs.
- Vapi (`vapi`): the Server URL that serves the prompt also takes `end-of-call-report` and the live
  `conversation-update` and `transcript` messages.

Each converts into the canonical session format and goes through `Sessions` (ingest, or open, append and close), so
the pipeline, the live list and the checks see nothing new. A session that streamed in and then arrives whole (the
post-call webhook) is the same session: the full post wins.

`Capture` holds the settings: secrets in `platform_secrets` (encrypted, never returned), the rest in
`capture_settings`. The monitors run as tasks in this process, or on Hatchet when it's configured (`sopops.jobs`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel
from sqlmodel import Session, select

from ..errors import workspace_id
from ..orm import CaptureSetting, PlatformSecret, SessionRecord, Workspace
from ..runtime import crypto

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops.capture")

# platform_secrets kinds
LIVEKIT_KEY, LIVEKIT_SECRET = "api_key", "api_secret"
POSTCALL_SECRET = "postcall_secret"


class LiveRoomRef(BaseModel):
    """A LiveKit room to record, from a `participant_joined` webhook for an agent."""

    workspace_id: str
    room: str
    room_sid: str | None = None
    agent_identity: str | None = None
    agent_name: str | None = None


class LiveConversationRef(BaseModel):
    """An ElevenLabs conversation to follow on the monitor socket, from the conversation-initiation webhook."""

    workspace_id: str
    conversation_id: str
    agent: str | None = None
    release: int | None = None


class Capture:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- settings ------------------------------------------------------------------------

    def setting(self, workspace: str, platform: str) -> CaptureSetting | None:
        with self._db() as db:
            return db.get(CaptureSetting, (workspace_id(workspace), platform))

    def update(self, workspace: str, platform: str, **fields: Any) -> CaptureSetting:
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            row = db.get(CaptureSetting, (ws, platform))
            if row is None:
                row = CaptureSetting(workspace=ws, platform=platform, agent_map={}, updated_at=_now())
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = _now()
            db.add(row)
        return row

    def forget(self, workspace: str, platform: str, kinds: Iterable[str]) -> None:
        """Remove the platform's settings and these secrets."""
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            for kind in kinds:
                if (s := db.get(PlatformSecret, (ws, platform, kind))) is not None:
                    db.delete(s)
            if (row := db.get(CaptureSetting, (ws, platform))) is not None:
                db.delete(row)

    def note_event(self, workspace: str, platform: str) -> None:
        """A signed webhook arrived: shown in Settings as the sign the webhook works."""
        try:
            self.update(workspace, platform, event_at=_now())
        except Exception:  # never a reason to refuse the webhook
            log.exception("couldn't note a %s webhook for %s", platform, workspace)

    def note_live(self, workspace: str, state: str, detail: str | None = None) -> None:
        """How ElevenLabs live monitoring last went: `on`, or `unavailable` with the reason in words."""
        try:
            self.update(workspace, "elevenlabs", live_state=state, live_detail=detail, live_checked_at=_now())
        except Exception:
            log.exception("couldn't note the live monitoring state for %s", workspace)

    # --- secrets (encrypted; decrypted only to verify a webhook or call the platform) -----------

    def secret(self, workspace: str, platform: str, kind: str) -> str | None:
        with self._db() as db:
            row = db.get(PlatformSecret, (workspace_id(workspace), platform, kind))
        return crypto.decrypt(row.ciphertext) if row else None

    def secret_info(self, workspace: str, platform: str, kind: str) -> dict:
        with self._db() as db:
            row = db.get(PlatformSecret, (workspace_id(workspace), platform, kind))
        return {"set": row is not None, "hint": row.hint if row else None, "created_at": _utc(row.created_at) if row else None}

    def set_secret(self, workspace: str, platform: str, kind: str, value: str) -> None:
        ws = workspace_id(workspace)
        value = value.strip()
        row = PlatformSecret(workspace=ws, platform=platform, kind=kind, ciphertext=crypto.encrypt(value), hint=crypto.hint(value), created_at=_now())
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            if (old := db.get(PlatformSecret, (ws, platform, kind))) is not None:
                db.delete(old)
                db.flush()
            db.add(row)

    def delete_secret(self, workspace: str, platform: str, kind: str) -> None:
        with self._db() as db, db.begin():
            if (row := db.get(PlatformSecret, (workspace_id(workspace), platform, kind))) is not None:
                db.delete(row)

    def next_seq(self, workspace: str, platform: str, external_id: str) -> int:
        """The `seq` after everything an open session holds, for one more event."""
        with self._db() as db:
            q = select(SessionRecord.turns, SessionRecord.tool_calls).where(
                SessionRecord.workspace_id == workspace_id(workspace), SessionRecord.platform == platform, SessionRecord.external_id == external_id
            )
            row = db.exec(q).first()
        seqs = [x.get("seq", -1) for x in (*row[0], *row[1])] if row else []
        return max(seqs, default=-1) + 1

    # --- agents --------------------------------------------------------------------------

    def known_agent(self, workspace: str, platform: str, ref: str | None) -> str | None:
        """The sopc agent `ref` names in the live release (its id, platform ref or id on `platform`), else None."""
        if not ref:
            return None
        ws = workspace_id(workspace)
        with self._db() as db:
            number = db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
            return self.store.sessions._match_agent(db, ws, ref, platform, number) if number else None

    def livekit_agent(self, workspace: str, agent_name: str | None, metadata: Iterable[str | dict | None]) -> tuple[str | None, int | None]:
        """The sopc agent for a LiveKit room, and a release pinned for it: the agent name it was dispatched by, else
        `{"sopops": {"agent": ...}}` in job or room metadata, else the workspace's mapping (that name, or `*`). With
        no match, the agent name as is (the session keeps it as sent)."""
        pins = [_sopops_meta(m) for m in metadata]
        release = next((p["release"] for p in pins if isinstance(p.get("release"), int)), None)
        if found := self.known_agent(workspace, "livekit", agent_name):
            return found, release
        for p in pins:
            if found := self.known_agent(workspace, "livekit", p.get("agent") if isinstance(p.get("agent"), str) else None):
                return found, release
        row = self.setting(workspace, "livekit")
        mapping = row.agent_map if row else {}
        mapped = mapping.get(agent_name or "") or mapping.get("*")
        if isinstance(mapped, str) and mapped:
            return mapped, release
        return agent_name or None, release


def _sopops_meta(raw: str | dict | None) -> dict:
    """`{"sopops": {"agent": ..., "release": ...}}` in job or room metadata (JSON), plus the SDK's own
    `{"sopops_release": 3}` pin."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (ValueError, RecursionError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out = dict(raw["sopops"]) if isinstance(raw.get("sopops"), dict) else {}
    if "release" not in out and raw.get("sopops_release") is not None:
        out["release"] = raw["sopops_release"]
    try:
        if out.get("release") is not None:
            out["release"] = int(out["release"])
    except (TypeError, ValueError):
        out.pop("release")
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
