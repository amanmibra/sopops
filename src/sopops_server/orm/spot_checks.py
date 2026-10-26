"""The `spot_checks` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel


class SpotCheck(SQLModel, table=True):
    """One rule result drawn into a spot-check batch for a reviewer to look at. A batch is the rows that share a
    `batch_id`; it's answered when a label lands on the rule (`answered_at`, `label_id`). See `sopops_server.labels`."""

    __tablename__ = "spot_checks"
    __table_args__ = (
        Index("ix_spot_checks_workspace_batch", "workspace_id", "batch_id"),
        Index("ix_spot_checks_session_rule", "session_id", "procedure_id", "rule_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_spot_checks_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    batch_id: str = Field(max_length=36)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    rule_result_id: str = Field(max_length=36, description="The rule result as it was when drawn.")
    weight: float = Field(description="Its value when drawn (`labels.weight`).")
    why: str = Field(max_length=64, description="Comma-separated: close_call, review_changed, or sample.")
    drawn_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    answered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    label_id: str | None = Field(default=None, max_length=36)
