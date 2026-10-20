"""The `backfills` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text

from .columns import JSONDoc


class Backfill(SQLModel, table=True):
    """One run of bringing a platform's past calls in as sessions (`sopops_server.capture.backfill`), with its progress.

    `found` finished calls in the window, `imported` new sessions, `skipped` calls already in sopops, `failed` calls
    that couldn't be read or converted. `agents` has the same counts per connected agent, with its error in words."""

    __tablename__ = "backfills"
    __table_args__ = (Index("ix_backfills_workspace_platform_created", "workspace", "platform", "created_at"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace: str = Field(max_length=128)
    platform: str = Field(max_length=32)
    days: int
    status: str = Field(max_length=16)  # running | done | failed
    found: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    agents: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONDoc, nullable=False))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_by: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
