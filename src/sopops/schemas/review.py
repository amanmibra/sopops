"""The review queue: patterns that need a verdict."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import BaseModel
from .checks import RuleStatus
from .patterns import PatternKind, RuleRef
from .suggestions import SuggestionSummary
from .verdicts import REVIEW_STATE_HELP, ReviewState, VerdictOut


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
    clustered_at: datetime | None = Field(description="Rule patterns: the last clustering. Sub-patterns: the last recluster that changed their findings materially.")
    procedure_name: str
    rule: RuleRef
    rule_text: str | None
    examples: list[ReviewExample] = Field(description="Up to 5, newest first (suppressed ones last).")
    verdict: VerdictOut | None
    new_findings: int = Field(description="Findings that aren't suppressed since the verdict (all of them without one).")
    review_state: ReviewState = Field(description=REVIEW_STATE_HELP)
    review_note: str | None = Field(description="Why it's in that state, e.g. \"Came back after release 7: 3 findings ...\".")
    fix_release: int | None = Field(description="The release with the applied edit, once there is one.")
    fix_sessions: int | None = Field(None, description="Fixing (release live) and fixed: sessions checked on the procedure on the fix's release or later.")
    fix_sessions_needed: int | None = Field(None, description="With fix_sessions: how many it takes, with no finding in the pattern, to count as fixed.")
    suppressed_count: int
    suggestion: SuggestionSummary | None = Field(description="The latest suggestion for the pattern.")


class ReviewResponse(BaseModel):
    items: list[ReviewItem]
    total: int = Field(description="Patterns in the states asked for (items is at most `limit` of them).")
    counts: dict[ReviewState, int] = Field(description="Patterns in each review state.")
    clustered_at: datetime | None = Field(description="The workspace's last clustering of any rule.")


class VerdictSaved(VerdictOut):
    suppressed_count: int = Field(description="The pattern's suppressed findings now.")
    suggestion: SuggestionSummary | None = Field(description="The suggestion queued for it, when `suggest` and the verdict calls for one.")
