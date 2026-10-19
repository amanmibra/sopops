"""The `verdicts` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text


class Verdict(SQLModel, table=True):
    """A reviewer's verdict on a pattern (a rule pattern or a sub-pattern). Rows are only appended: a pattern's current
    verdict is its latest row, the rest is its history. A sub-pattern without one of its own has its rule pattern's.
    See `sopops_server.verdicts`."""

    __tablename__ = "verdicts"
    __table_args__ = (Index("ix_verdicts_workspace_pattern", "workspace_id", "pattern_id", "created_at"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    pattern_id: str = Field(max_length=36)
    verdict: str = Field(max_length=16)  # real | acceptable | sop_wrong | checker_wrong
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    reviewer: str | None = Field(default=None, max_length=256, description="Free text until there's auth.")
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`reviewer` keeps the name).")
    carried_from: str | None = Field(default=None, max_length=36, description="The retired pattern a recluster carried this verdict over from.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
