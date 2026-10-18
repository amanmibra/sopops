"""The `rule_results` table."""

from __future__ import annotations

from typing import Any

from sqlmodel import (
    Column,
    Field,
    ForeignKeyConstraint,
    Index,
    SQLModel,
    Text,
    UniqueConstraint,
)

from .columns import JSONDoc


class RuleResult(SQLModel, table=True):
    """One session x one rule (step, never or warning sign) of a checked procedure."""

    __tablename__ = "rule_results"
    __table_args__ = (
        UniqueConstraint("session_id", "procedure_id", "rule_id", name="uq_rule_results_session_rule"),
        Index("ix_rule_results_procedure_rule", "workspace_id", "procedure_id", "rule_id", "session_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_rule_results_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64, description="sopc's stable rule id (lock.json, `sopc export`).")
    kind: str = Field(max_length=16)  # step | never | warning
    index: int = Field(description="1-based position within its kind.")
    rule_text: str = Field(sa_column=Column(Text, nullable=False))
    rule_text_hash: str = Field(max_length=64)
    status: str = Field(max_length=16)  # step: done|out_of_order|skipped|not_reached|not_applicable; never: violated|ok; warning: present|unheeded|absent
    evidence: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))  # [{turn, quote, source}]
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
