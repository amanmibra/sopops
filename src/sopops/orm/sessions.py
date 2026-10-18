"""The `sessions` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import (
    Column,
    DateTime,
    Field,
    ForeignKeyConstraint,
    Index,
    SQLModel,
    Text,
    UniqueConstraint,
)

from .columns import JSONDoc


class SessionRecord(SQLModel, table=True):
    """One conversation an agent had: a voice call or a text chat. Unique on (workspace, platform, external_id), so
    re-sending a session (a retried webhook, a re-run import) updates it instead of adding a second one."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "platform", "external_id", name="uq_sessions_key"),
        Index("ix_sessions_workspace_started", "workspace_id", "started_at"),
        Index("ix_sessions_workspace_release", "workspace_id", "release_id"),
        Index("ix_sessions_workspace_agent_started", "workspace_id", "agent_id", "started_at"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace"], name="fk_sessions_workspace"),
        ForeignKeyConstraint(["workspace_id", "release_id"], ["releases.workspace", "releases.number"], name="fk_sessions_release"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    agent_id: str | None = Field(default=None, max_length=256, description="The sopc agent, when the payload's agent could be matched.")
    agent_ref: str | None = Field(default=None, max_length=256, description="The agent as the payload named it.")
    channel: str = Field(max_length=16)  # voice | text
    platform: str = Field(max_length=32)  # livekit | elevenlabs | vapi | otel | import | api (schemas.Platform)
    external_id: str = Field(max_length=256)
    release_id: int | None = Field(default=None, description="The sopops release the session ran on.")
    version_ref: str | None = Field(default=None, max_length=256, description="The platform's own version id.")
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    status: str = Field(max_length=16)  # open | received | routed | checked | excluded | incomplete | failed
    status_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # [{role: agent|user|tool|system, text, offset_s, ...}], in order
    turns: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))
    tool_calls: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))
    meta: dict[str, Any] = Field(sa_column=Column("metadata", JSONDoc, nullable=False))
    payload_hash: str = Field(max_length=64, description="sha256 of the canonical payload: an identical re-send changes nothing.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    # The worst procedure result once checked: deviated | partial | followed | not_measurable (see pipeline.rollup)
    result: str | None = Field(default=None, max_length=16)
