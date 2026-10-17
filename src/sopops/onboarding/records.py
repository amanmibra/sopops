"""Onboarding tables: one row per import (an LLM job run here, or a coding agent working through an onboarding token),
and its progress events. Migration `0006_onboarding`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, Text
from sqlmodel import Field, SQLModel


class ImportRecord(SQLModel, table=True):
    """An import of existing agent prompts into a workspace.

    `source` is `llm` (sopops extracts with its own model) or `agent` (a coding agent such as Claude Code extracts
    in the user's repo and pushes a proposal with an onboarding token). Either way the result is a proposal the user
    reviews, then applies as drafts.
    """

    __tablename__ = "imports"
    __table_args__ = (Index("ix_imports_workspace_created", "workspace", "created_at"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace: str = Field(max_length=128)
    source: str = Field(max_length=16)  # llm | agent
    status: str = Field(max_length=16)  # waiting | running | ready | failed | applied | discarded
    mode: str = Field(default="hosted", max_length=16)  # hosted | repo
    repo: str | None = Field(default=None, max_length=1024, description="Repo mode: where the sopc project goes.")
    agents: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))  # names, for lists
    proposal: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    pushes: int = 0
    # The onboarding token (source `agent`): a sha256 hash, shown once. Valid until it expires or is revoked.
    token_hash: str | None = Field(default=None, max_length=64, unique=True)
    token_prefix: str | None = Field(default=None, max_length=32)
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_by: str | None = Field(default=None, max_length=256)


class ImportEvent(SQLModel, table=True):
    """A progress message: from the extraction steps here, or from the coding agent (`report_progress`)."""

    __tablename__ = "import_events"

    id: int | None = Field(default=None, primary_key=True)
    import_id: str = Field(max_length=36, index=True)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    step: str = Field(max_length=64)
    message: str = Field(sa_column=Column(Text, nullable=False))
    by: str = Field(max_length=16)  # sopops | agent
