"""The `finding_overrides` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel, Text


class FindingOverride(SQLModel, table=True):
    """A reviewer's call on one finding: the checker was right (`correct`) or not. Keyed by session, procedure and rule
    as well as the finding, so it outlives a re-check of the session (which writes new findings). Rows are only
    appended; the latest per (session, procedure, rule) wins."""

    __tablename__ = "finding_overrides"
    __table_args__ = (
        Index("ix_finding_overrides_session_rule", "session_id", "procedure_id", "rule_id"),
        Index("ix_finding_overrides_workspace", "workspace_id", "created_at"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_finding_overrides_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    finding_id: str = Field(max_length=36, description="The finding as it was when the call was made.")
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    correct: bool
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    reviewer: str | None = Field(default=None, max_length=256)
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`reviewer` keeps the name).")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
