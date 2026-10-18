"""The `suggestions` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text

from .columns import JSONDoc


class Suggestion(SQLModel, table=True):
    """A proposed edit to one procedure, made by the reasoning model from a pattern and its verdict, checked by
    compiling it with sopc. `plan` is what the edit does to the compiled prompts. See `sopops.suggestions`."""

    __tablename__ = "suggestions"
    __table_args__ = (
        Index("ix_suggestions_workspace_status", "workspace_id", "status"),
        Index("ix_suggestions_pattern", "pattern_id"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    pattern_id: str = Field(max_length=36)
    verdict_id: str | None = Field(default=None, max_length=36, description="The verdict it was made for.")
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    path: str | None = Field(default=None, max_length=512, description="The procedure's file.")
    base_version: int | None = Field(default=None, description="The file's head version it was made from.")
    old_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    new_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    rationale: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    plan: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONDoc, nullable=True))
    status: str = Field(max_length=16)  # generating | proposed | applied | pr_open | merged | dismissed | failed
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    pr_url: str | None = Field(default=None, max_length=1024)
    pr_checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    applied_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    pr_opened_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    merged_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))  # when sopops saw it merged
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True), description="Why it was dismissed (or that it was rewritten).")
    reviewer: str | None = Field(default=None, max_length=256)
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`reviewer` keeps the name).")
    attempts: int = 0
    model: str | None = Field(default=None, max_length=256)
    prompt_version: str | None = Field(default=None, max_length=64)
    merged_into_version: int | None = Field(default=None, description="The file's version the edit was merged into when applied, when the "
                                            "procedure had changed since it was written (`sopops.merge`).")
    rewrite_of: str | None = Field(default=None, max_length=36, description="The suggestion this one rewrites against the current text.")
    applied_version: int | None = Field(default=None, description="The file version the draft edit wrote when applied. A release with this "
                                        "version of the file or a later one has the edit, even after later edits built on it.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
