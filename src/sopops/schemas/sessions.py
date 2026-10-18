"""Sessions: the canonical ingest format, summaries, details and the live event stream."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict, Field

from .base import BaseModel
from .checks import CheckResultValue, ProcedureCheckOut

Channel = Literal["voice", "text"]


# Where a session came from: a voice platform (LiveKit first), or a transport (OpenTelemetry, a file import, the API).
# Add a platform here (and to the UI's list) to accept it; the column is a plain string.
Platform = Literal["livekit", "elevenlabs", "vapi", "otel", "import", "api"]


# open: streaming in (sessions/open, then events), not checked until it closes.
SessionStatus = Literal["open", "received", "routed", "checked", "excluded", "partial", "failed"]


class TurnIn(_BaseModel):
    """One turn. Extra fields (interrupted, confidence, ...) are kept as sent."""

    model_config = ConfigDict(extra="allow")

    role: str = Field(description="`agent`, `user`, `tool` or `system`. Common aliases (`assistant`, `bot`, `human`, `customer`, `function`) are mapped.")
    text: str = ""
    offset_s: float | None = Field(None, description="Seconds since the session started.")
    at: datetime | float | None = Field(None, description="When the turn happened, if `offset_s` isn't known: ISO 8601, or Unix seconds or milliseconds. Turned into `offset_s`.")


class ToolCallIn(_BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    arguments: Any = None
    result: Any = None
    is_error: bool = False
    call_id: str | None = None
    offset_s: float | None = None
    at: datetime | float | None = None


class SessionIn(_BaseModel):
    """A finished session in the canonical format. Every transport (helpers, webhooks, imports) converts into this."""

    platform: Platform
    external_id: str = Field(min_length=1, max_length=256, description="The platform's id for the session (room or job id, conversation id, call id). With `platform`, the session's unique key.")
    channel: Channel = "voice"
    agent: str | None = Field(None, max_length=256, description="The sopc agent id, or the agent's platform id (`tonys-pizza`, `livekit:tonys-pizza`).")
    release: int | None = Field(None, description="The sopops release the agent ran on, as returned at session start.")
    version_ref: str | None = Field(None, max_length=256, description="The platform's own version of the agent (ElevenLabs `version_id`, Vapi `assistantVersion`).")
    started_at: datetime | None = Field(None, description="Default: the first turn's `at`, else when sopops received it.")
    ended_at: datetime | None = None
    partial: bool = Field(False, description="The sender knows the transcript is incomplete.")
    turns: list[TurnIn] = Field(default_factory=list, description="In order.")
    tool_calls: list[ToolCallIn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionOpen(_BaseModel):
    """Open a session to stream its turns and tool calls as they happen (optional). The final full post to
    `…/sessions` still wins; until then, or until `…/close`, the session isn't checked."""

    platform: Platform
    external_id: str = Field(min_length=1, max_length=256)
    channel: Channel = "voice"
    agent: str | None = Field(None, max_length=256)
    release: int | None = None
    version_ref: str | None = Field(None, max_length=256)
    started_at: datetime | None = Field(None, description="Default: when sopops received it.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionEventIn(_BaseModel):
    """One turn or tool call of an open session. `seq` counts from 0 per session: a resent `seq` is ignored, and
    events can arrive in any order (they're kept in `seq` order)."""

    seq: int = Field(ge=0)
    type: Literal["turn", "tool_call"]
    turn: TurnIn | None = None
    tool_call: ToolCallIn | None = None


class SessionEventsIn(_BaseModel):
    events: list[SessionEventIn] = Field(max_length=500)


class SessionEventsOut(BaseModel):
    accepted: int
    duplicates: int = Field(description="Events whose seq was already stored, or sent after the session closed.")


class SessionClose(_BaseModel):
    ended_at: datetime | None = None
    partial: bool = Field(False, description="The sender knows the transcript is incomplete.")


class SessionIngested(BaseModel):
    id: str
    created: bool = Field(description="A new session.")
    updated: bool = Field(description="An existing session whose content changed. Both false: an identical re-send, nothing changed.")
    status: SessionStatus
    warnings: list[str] = Field(description="E.g. a release that doesn't exist (the session is kept without it).")


class SessionSummary(BaseModel):
    id: str
    workspace: str
    agent_id: str | None = Field(description="The sopc agent, if the payload's agent matched one.")
    agent_ref: str | None = Field(description="The agent as sent.")
    channel: Channel
    platform: Platform
    external_id: str
    release_id: int | None
    version_ref: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: SessionStatus
    status_reason: str | None
    result: CheckResultValue | None = Field(description="The worst procedure result once checked: deviated, partial, followed or not_measurable.")
    turn_count: int
    tool_call_count: int
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = Field(description="When the pipeline last finished with it.")


class SessionTurn(_BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    role: Literal["agent", "user", "tool", "system"]
    text: str
    offset_s: float | None


class SessionDetail(SessionSummary):
    turns: list[SessionTurn]
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any]
    checks: list[ProcedureCheckOut] = Field(description="Each procedure the router considered, with its rule results.")


class SessionPage(BaseModel):
    items: list[SessionSummary]
    next_cursor: str | None = Field(description="Pass as `cursor` for the next (older) page; null on the last page.")


class SessionEvent(BaseModel):
    """What `…/sessions/stream` sends, as `data:` of an SSE `session` event."""

    type: Literal["created", "updated"]
    workspace: str
    id: str
    status: SessionStatus
