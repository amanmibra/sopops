"""The `rule_labels` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel, Text


class RuleLabel(SQLModel, table=True):
    """A reviewer's status for one rule of a checked session: a correction (`correction`, from the session page) or an
    answered spot check (`spot_check`). It works on any rule result, with a finding or without one. Keyed by session,
    procedure and rule, so it outlives a re-check; rows are only appended, and the latest per (session, procedure,
    rule) wins. See `sopops_server.labels`."""

    __tablename__ = "rule_labels"
    __table_args__ = (
        Index("ix_rule_labels_session_rule", "session_id", "procedure_id", "rule_id"),
        Index("ix_rule_labels_workspace", "workspace_id", "created_at"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_rule_labels_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    rule_result_id: str | None = Field(default=None, max_length=36, description="The rule result as it was when labeled (a re-check replaces it).")
    kind: str = Field(max_length=16)  # step | never | warning
    checked_status: str = Field(max_length=16, description="What the checker said when it was labeled.")
    status: str = Field(max_length=16, description="What the reviewer says actually happened.")
    source: str = Field(max_length=16)  # correction | spot_check
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    reviewer: str | None = Field(default=None, max_length=256)
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`reviewer` keeps the name).")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
