"""API shapes for runtime delivery (kept apart from `sopops_server.schemas`)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DeliveryMode = Literal["pull", "push", "both"]
PushPlatform = Literal["elevenlabs", "vapi"]
SecretKind = Literal["api-key", "webhook-secret"]


class AgentDeliveryOut(BaseModel):
    agent: str
    platform: str
    platform_id: str
    mode: DeliveryMode = Field(description="Its platform's delivery mode.")
    modes: list[DeliveryMode] = Field(description="The modes its platform allows (LiveKit: pull only).")


class DeliveryIn(BaseModel):
    mode: DeliveryMode


class SecretInfo(BaseModel):
    set: bool
    hint: str | None = Field(None, description="The last few characters.")
    created_at: datetime | None


class PlatformCredentials(BaseModel):
    platform: PushPlatform
    api_key: SecretInfo
    webhook_secret: SecretInfo
    delivery: DeliveryMode = Field(description="How the platform's agents get their instructions: `pull` at call start, `push` on publish, or `both`.")


class SecretIn(BaseModel):
    value: str | None = Field(None, description="The API key, or the webhook secret. A webhook secret left empty is generated.")


class SecretSaved(BaseModel):
    platform: PushPlatform
    kind: Literal["api_key", "webhook_secret"]
    hint: str
    created_at: datetime
    secret: str | None = Field(None, description="A generated webhook secret, shown this once.")


class KeyTestIn(BaseModel):
    platform_id: str | None = Field(None, description="The agent to read; default: the first agent at head on this platform.")


class KeyTestOut(BaseModel):
    ok: bool
    platform_id: str
    message: str


class PushResultOut(BaseModel):
    release: int
    agent: str
    platform: str
    platform_id: str
    status: Literal["ok", "unchanged", "error"]
    version_id: str | None
    error: str | None
    attempts: int
    at: datetime


class RetryIn(BaseModel):
    agents: list[str] | None = Field(None, description="Push these agents again; default: every agent whose last push failed.")


class ServedIn(BaseModel):
    platform: PushPlatform
    call_id: str = Field(min_length=1, max_length=256, description="The platform's id for the call: an ElevenLabs conversation id, a Vapi call id.")
    agent: str = Field(description="The sopc agent id or platform ref the instructions were fetched for.")
    release: int


class ServedOut(BaseModel):
    platform: PushPlatform
    call_id: str
    agent: str
    release: int
