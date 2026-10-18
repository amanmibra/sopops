"""Patterns and themes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .checks import RuleKind
from .suggestions import SuggestionSummary
from .verdicts import REVIEW_STATE_HELP, ReviewState, VerdictOut

PatternKind = Literal["rule", "sub", "other"]


Interval = tuple[int, int]


class FiltersOut(BaseModel):
    start: datetime
    end: datetime
    agent: str | None
    procedure: str | None
    releases: list[int]
    channel: str | None


class ExampleOut(BaseModel):
    session_id: str
    turn: int | None
    quote: str
    status: str


class PatternOut(BaseModel):
    pattern_id: str
    procedure_id: str
    rule_id: str
    parent_id: str | None
    kind: PatternKind = Field(description="`rule`: every finding on a rule; `sub`: a named failure mode of it; `other`: findings that fit none.")
    name: str
    description: str | None
    theme_id: str | None
    status: Literal["active", "retired"]
    findings: int = Field(description="Findings in the window.")
    sessions: int = Field(description="Sessions with a finding in the window.")
    total_findings: int = Field(description="All time.")
    suppressed: int = Field(0, description="Suppressed findings in the window (not in `findings` or `sessions`).")
    first_seen: datetime | None
    last_seen: datetime | None
    theme_name: str | None = None
    examples: list[ExampleOut] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list, description="In the report: the most recent sessions (up to 12).")
    need_attention_pct: int | None = Field(None, description="In the report: sessions in it that need attention / all that do.")
    clustered_at: datetime | None = Field(None, description="Rule patterns: the last clustering. Sub-patterns: the last recluster that changed their findings materially.")
    verdict: VerdictOut | None = Field(None, description="Its current verdict (its rule pattern's, when it has none of its own). On the patterns list and a pattern's page.")
    review_state: ReviewState | None = Field(None, description=f"On the patterns list and a pattern's page. {REVIEW_STATE_HELP}")
    review_note: str | None = None


class RuleRef(BaseModel):
    kind: RuleKind | None
    index: int | None
    text: str | None


class RulePatternOut(PatternOut):
    sub_patterns: list[PatternOut]
    rule: RuleRef | None = None


class PatternsResponse(BaseModel):
    items: list[RulePatternOut]
    clustered_at: datetime | None = Field(None, description="The workspace's last clustering of any rule.")


class PatternDetail(PatternOut):
    rule: RuleRef
    sub_patterns: list[PatternOut]
    verdict_history: list[VerdictOut] = Field(default_factory=list, description="Its own verdicts, newest first.")
    suppressed_count: int = Field(0, description="Suppressed findings, all time.")
    suggestions: list[SuggestionSummary] = Field(default_factory=list, description="Newest first.")
    fix_release: int | None = Field(None, description="The release with the applied edit, once there is one.")
    fix_sessions: int | None = Field(None, description="Fixing (release live) and fixed: sessions checked on the procedure on the fix's release or later.")
    fix_sessions_needed: int | None = Field(None, description="With fix_sessions: how many it takes, with no finding in the pattern, to count as fixed.")


class ReclusterRequest(BaseModel):
    procedure: str | None = Field(None, description="Only this procedure's rules.")
    rule: str | None = Field(None, description="Only this rule (with `procedure`).")
    force: bool = Field(False, description="Recluster even if no trigger fired (a rule still needs 8 findings).")


class ReclusterResponse(BaseModel):
    assigned: int
    reclustered: list[str] = Field(description="Rule ids.")
    themed: list[str] = Field(description="Procedure ids whose themes were regrouped.")


class ThemeOut(BaseModel):
    theme_id: str | None = Field(description="Null for patterns not grouped into a theme yet.")
    name: str
    description: str | None
    findings: int
    sessions: int
    patterns: list[RulePatternOut]
    need_attention_pct: int | None = Field(None, description="In the report: sessions with a finding in the theme that need attention / all that do.")
