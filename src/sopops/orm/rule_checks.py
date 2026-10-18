"""The `rule_checks` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text

from .columns import JSONDoc


class RuleChecks(SQLModel, table=True):
    """A rule rewritten as literal yes/no checks for Jev (`sopops.ai.signatures.SplitRule`), once per rule version:
    keyed by sopc's rule id and a hash of the rule's text, so an edited rule is split again."""

    __tablename__ = "rule_checks"

    workspace_id: str = Field(primary_key=True, max_length=128)
    rule_id: str = Field(primary_key=True, max_length=64)
    text_hash: str = Field(primary_key=True, max_length=64)
    kind: str = Field(max_length=16)
    condition: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    actions: list[str] = Field(sa_column=Column(JSONDoc, nullable=False))
    forbidden: list[str] = Field(sa_column=Column(JSONDoc, nullable=False))
    model: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
