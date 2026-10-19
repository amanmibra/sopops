"""The `findings` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel


class Finding(SQLModel, table=True):
    """A rule result that is a deviation (see `pipeline.rollup.FINDING_STATUSES`). `pattern_id`: its most specific
    pattern (a sub-pattern once its rule is split, else the rule pattern); null until assigned. `suppressed` is kept in step
with verdicts and overrides by `sopops_server.verdicts`."""

    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_workspace_rule", "workspace_id", "procedure_id", "rule_id"),
        Index("ix_findings_pattern", "pattern_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_findings_session", ondelete="CASCADE"),
        ForeignKeyConstraint(["rule_result_id"], ["rule_results.id"], name="fk_findings_rule_result", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    rule_result_id: str = Field(max_length=36)
    status: str = Field(max_length=16)
    pattern_id: str | None = Field(default=None, max_length=36)
    suppressed: bool = Field(default=False, description="A verdict or an override says it isn't a problem: kept, but out of every Insights count and rate.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
