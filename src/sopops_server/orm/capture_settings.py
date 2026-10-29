"""The `capture_settings` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, DateTime, Field, SQLModel, Text

from .columns import JSONDoc


class CaptureSetting(SQLModel, table=True):
    """How sessions reach sopops from a platform with no code in the agent, per workspace and platform. Secrets (the
    LiveKit key pair, the ElevenLabs post-call secret) are in `platform_secrets`, never here.

    LiveKit: the project `url` and `agent_map` (a LiveKit agent name, or `*` for any, to a sop agent). ElevenLabs:
    `live` turns on Enterprise live monitoring, and `live_state` is how it last went (`on` or `unavailable`)."""

    __tablename__ = "capture_settings"

    workspace: str = Field(primary_key=True, max_length=128)
    platform: str = Field(primary_key=True, max_length=32)
    url: str | None = Field(None, max_length=1024)
    agent_map: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONDoc, nullable=False))
    live: bool = Field(False)
    live_state: str | None = Field(None, max_length=16)
    live_detail: str | None = Field(None, sa_column=Column(Text, nullable=True))
    live_checked_at: datetime | None = Field(None, sa_column=Column(DateTime(timezone=True), nullable=True))
    event_at: datetime | None = Field(None, sa_column=Column(DateTime(timezone=True), nullable=True), description="The last signed webhook received.")
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
