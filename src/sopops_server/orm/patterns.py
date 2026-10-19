"""The `patterns` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text


class Pattern(SQLModel, table=True):
    """Findings grouped. Every rule with findings has one `rule` pattern (parent_id null). Once it has enough findings,
    a model splits it into named failure modes: `sub` patterns, plus one `other` sub-pattern for findings that fit none.
    A finding's `pattern_id` is its most specific pattern. Ids and names stay stable across reclustering (new clusters
    are matched to old ones by member overlap), so verdicts attached to a pattern keep applying."""

    __tablename__ = "patterns"
    __table_args__ = (
        Index("ix_patterns_workspace_rule", "workspace_id", "procedure_id", "rule_id"),
        Index("ix_patterns_parent", "parent_id"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    parent_id: str | None = Field(default=None, max_length=36, description="The rule pattern, for a sub-pattern.")
    kind: str = Field(max_length=8)  # rule | sub | other
    name: str = Field(max_length=256)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    theme_id: str | None = Field(default=None, max_length=36)
    status: str = Field(default="active", max_length=16)  # active | retired (a recluster found no cluster like it)
    finding_count: int = 0
    session_count: int = 0
    first_seen: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    last_seen: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    clustered_count: int = Field(default=0, description="Rule patterns: findings on the rule at the last clustering.")
    # rule patterns: the last clustering; sub-patterns: the last recluster that changed their members materially
    clustered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    model: str | None = Field(default=None, max_length=256)
    prompt_version: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
