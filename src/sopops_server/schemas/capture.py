"""API shapes for sessions with no code in the agent (`sopops_server.capture`), kept apart from `sopops_server.schemas`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .runtime import SecretInfo


class LiveKitIn(BaseModel):
    url: str = Field(max_length=1024, description="The LiveKit Cloud project URL (`wss://<project>.livekit.cloud`).")
    api_key: str | None = Field(None, max_length=256, description="Needed to connect; left out, the stored one is kept.")
    api_secret: str | None = Field(None, max_length=512, description="Needed to connect; left out, the stored one is kept.")
    agent_map: dict[str, str] | None = Field(
        None, description="A LiveKit agent name (or `*` for any) to a sopc agent, for rooms whose agent name and metadata don't name one. Left out: kept."
    )

    @field_validator("agent_map")
    @classmethod
    def _small(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None:
            if len(v) > 100:
                raise ValueError("at most 100 entries")
            if any(not k.strip() or len(k) > 256 or not s.strip() or len(s) > 256 for k, s in v.items()):
                raise ValueError("names are 1 to 256 characters")
            v = {k.strip(): s.strip() for k, s in v.items()}
        return v


class LiveKitCapture(BaseModel):
    connected: bool = Field(description="A project URL, API key and secret are stored.")
    url: str | None
    api_key: SecretInfo
    api_secret: SecretInfo
    agent_map: dict[str, str]
    webhook_path: str = Field(description="Where LiveKit Cloud sends webhooks, under this server's address.")
    last_event_at: datetime | None = Field(description="The last signed webhook from LiveKit.")
    recorder_installed: bool = Field(description="This server can join rooms (`pip install 'sopops-server[livekit]'`).")


class ElevenLabsCapture(BaseModel):
    post_call_secret: SecretInfo
    post_call_path: str = Field(description="Where ElevenLabs sends the post-call webhook, under this server's address.")
    last_post_call_at: datetime | None
    api_key_set: bool = Field(description="Live monitoring uses the ElevenLabs API key (Platforms).")
    live: bool = Field(description="Enterprise live monitoring is on.")
    live_state: Literal["on", "unavailable"] | None = Field(description="How the last monitor went; null before the first.")
    live_detail: str | None
    live_checked_at: datetime | None


class VapiCapture(BaseModel):
    server_url_path: str = Field(description="The Server URL, under this server's address; the same as for assistant-request.")
    server_messages: list[str] = Field(description="Server messages to turn on in Vapi.")


class CaptureOut(BaseModel):
    livekit: LiveKitCapture
    elevenlabs: ElevenLabsCapture
    vapi: VapiCapture


class PostCallSecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=512, description="The HMAC secret ElevenLabs shows when the post-call webhook is made.")


class LiveIn(BaseModel):
    on: bool


class CaptureTestOut(BaseModel):
    ok: bool
    message: str


class WebhookReceipt(BaseModel):
    status: str
    session_id: str | None = None


# --- backfill (`sopops_server.capture.backfill`) ---------------------------------------------------------------


class BackfillAgentPreview(BaseModel):
    agent: str = Field(description="The sopc agent.")
    platform_id: str = Field(description="Its agent (ElevenLabs) or assistant (Vapi) id.")
    found: int = Field(description="Finished calls in the window.")
    already_imported: int
    to_import: int
    capped: bool = Field(description="The listing stopped at the page limit, so there may be more calls.")
    error: str | None = Field(description="Why this agent's calls couldn't be listed.")


class BackfillPreview(BaseModel):
    platform: Literal["elevenlabs", "vapi"]
    days: int
    key_set: bool = Field(description="An API key is stored and can be read.")
    error: str | None = Field(description="Why nothing could be listed (no API key).")
    agents: list[BackfillAgentPreview]
    found: int
    already_imported: int
    to_import: int
    cost_per_session_usd: float | None = Field(description="This team's model spend per checked session over the last 30 days; null without any.")
    estimated_cost_usd: float | None = Field(description="`to_import` times `cost_per_session_usd`: roughly what checking them costs.")


class BackfillAgent(BaseModel):
    agent: str
    platform_id: str
    found: int
    imported: int
    skipped: int = Field(description="Already in sopops.")
    failed: int
    capped: bool
    error: str | None


class BackfillOut(BaseModel):
    id: str
    platform: Literal["elevenlabs", "vapi"]
    days: int
    status: Literal["running", "done", "failed"]
    found: int = Field(description="Finished calls in the window, for every agent.")
    imported: int = Field(description="New sessions so far.")
    skipped: int = Field(description="Calls already in sopops.")
    failed: int = Field(description="Calls that couldn't be read or converted.")
    agents: list[BackfillAgent]
    error: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class BackfillIn(BaseModel):
    days: int = Field(7, ge=1, description="How many days back: up to 7, and more counts as 7.")
