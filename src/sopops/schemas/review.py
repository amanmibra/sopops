"""The review queue: patterns that need a verdict."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import BaseModel
from .checks import RuleStatus
from .patterns import PatternKind, RuleRef
from .suggestions import SuggestionSummary
from .verdicts import VerdictOut


class ReviewExample(BaseModel):
    finding_id: str
    session_id: str
    started_at: datetime
    status: RuleStatus
    excerpt: str | None = Field(description="The first quote from the transcript.")
    reason: str | None = Field(description="The checker's reason.")
    evidence: list[dict[str, Any]]
    suppressed: bool


class ReviewItem(BaseModel):
    pattern_id: str
    procedure_id: str
    rule_id: str
    parent_id: str | None
    kind: PatternKind
    name: str
    description: str | None
    theme_id: str | None
    theme_name: str | None
    status: Literal["active", "retired"]
    findings: int = Field(description="All time.")
    sessions: int = Field(description="All time.")
    first_seen: datetime | None
    last_seen: datetime | None
    procedure_name: str
    rule: RuleRef
    rule_text: str | None
    examples: list[ReviewExample] = Field(description="Up to 5, newest first (suppressed ones last).")
    verdict: VerdictOut | None
    new_findings: int = Field(description="Findings that aren't suppressed since the verdict (all of them without one).")
    suppressed_count: int
    suggestion: SuggestionSummary | None = Field(description="The latest suggestion for the pattern.")


class ReviewResponse(BaseModel):
    items: list[ReviewItem]
    total: int = Field(description="Patterns needing a verdict (items is at most `limit` of them).")


class VerdictSaved(VerdictOut):
    suppressed_count: int = Field(description="The pattern's suppressed findings now.")
    suggestion: SuggestionSummary | None = Field(description="The suggestion queued for it, when `suggest` and the verdict calls for one.")
