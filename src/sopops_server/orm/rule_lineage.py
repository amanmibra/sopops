"""The `rule_lineage` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel


class RuleLineage(SQLModel, table=True):
    """Which rule a rule replaced. sop derives a rule's id from its text, so a reworded rule gets a new id; this row
    says it is the same rule as `previous_rule_id` (null: a person said it's a new rule, so nothing is matched to it).
    `lineage_id` is the first rule id of the chain, kept on every row so a rule's whole lineage is one query; a rule
    with no row is its own lineage. Written at publish (`sopops_server.lineage`); sessions and findings keep the exact rule id
    they ran."""

    __tablename__ = "rule_lineage"
    __table_args__ = (
        Index("ix_rule_lineage_lineage", "workspace_id", "procedure_id", "lineage_id"),
        Index("ix_rule_lineage_previous", "workspace_id", "procedure_id", "previous_rule_id"),
    )

    workspace_id: str = Field(primary_key=True, max_length=128)
    procedure_id: str = Field(primary_key=True, max_length=256)
    rule_id: str = Field(primary_key=True, max_length=64)
    previous_rule_id: str | None = Field(default=None, max_length=64)
    lineage_id: str = Field(max_length=64)
    release_id: int | None = Field(default=None, description="The release that published the change (null for a manual link).")
    how: str = Field(max_length=16)  # suggestion | matched | manual
    similarity: float | None = Field(default=None, description="Token Jaccard of the two wordings, when both are known.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
