"""Insights: overview, procedure, rule, release compare and the report."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .checks import EvidenceOut, RuleKind
from .patterns import FiltersOut, Interval, RulePatternOut, ThemeOut


class TilesValues(BaseModel):
    sessions_checked: int = Field(description="Eligible sessions: checked (or partial) with a result.")
    followed: int = Field(description="Followed end to end: every procedure check in scope followed.")
    need_attention: int = Field(description="Some procedure check in scope deviated.")
    partial: int
    not_measurable: int
    excluded: int
    in_progress: int
    failed: int
    followed_pct: int | None
    need_attention_pct: int | None
    checks: int
    measurable_checks: int
    pass_rate: int | None = Field(description="Procedure checks followed / measurable checks.")
    goal_n: int
    goal_rate: int | None


class Tiles(BaseModel):
    current: TilesValues
    prior: TilesValues
    delta_points: dict[str, int | None]
    delta_pct: dict[str, int | None]


class TrendBucket(BaseModel):
    start: datetime
    followed: int
    partial: int
    deviated: int
    not_measurable: int
    pass_rate: int | None
    n: int


class ReleaseMarker(BaseModel):
    release: int
    first_session: datetime


class Trend(BaseModel):
    bucket: Literal["day", "week"]
    buckets: list[TrendBucket]
    releases: list[ReleaseMarker]


class FailingRule(BaseModel):
    rule_id: str
    kind: RuleKind | None
    index: int | None
    text: str | None
    n: int
    fails: int
    fail_rate: int | None


class ScoreboardRow(BaseModel):
    procedure_id: str
    procedure_name: str
    checks: int
    measurable: int
    followed: int
    partial: int
    deviated: int
    not_measurable: int
    excluded: int
    pass_rate: int | None
    prior_pass_rate: int | None
    prior_measurable: int
    delta_points: int | None
    top_failing_rules: list[FailingRule]
    never_violations: int
    open_patterns: int = Field(description="Patterns (each finding's most specific one) with findings in the window.")


class OverviewResponse(BaseModel):
    filters: FiltersOut
    tiles: Tiles
    trend: Trend
    scoreboard: list[ScoreboardRow]


class LadderRow(BaseModel):
    rule_id: str
    kind: RuleKind
    index: int
    text: str
    wordings: int = Field(description="How many wordings the rule has had; more than 1: it was reworded.")
    total: int
    counts: dict[str, int]
    n: int = Field(description="Passes + fails (not reached and not applicable left out). Warning signs: every judgment.")
    passes: int | None
    fails: int | None
    pass_rate: int | None
    fail_rate: int | None
    skip_rate: int | None
    present: int | None
    present_rate: int | None
    unheeded: int | None
    not_counted: int
    not_counted_pct: int | None
    findings: int
    pattern_id: str | None
    patterns: int


class SplitRow(BaseModel):
    rule_id: str
    a_n: int
    a_pass_rate: int | None
    b_n: int
    b_pass_rate: int | None
    delta_points: int | None = Field(description="Null unless both sides have at least min_n.")
    flag: Literal["thin", "regressed", "improved", "steady"]


class ReleaseSplit(BaseModel):
    a: int
    b: int
    min_n: int
    rules: list[SplitRow]


class WarningImpact(BaseModel):
    rule_id: str
    index: int
    text: str
    present_n: int
    present_pass_rate: int | None
    absent_n: int
    absent_pass_rate: int | None
    delta_points: int | None
    flag: bool = Field(description="The pass rate drops 10 points or more when the sign is present (min n on both sides).")
    unheeded: int


class ProcedureInsights(BaseModel):
    filters: FiltersOut
    procedure_id: str
    procedure_name: str
    tiles: Tiles
    trend: Trend
    rules: list[LadderRow]
    release_split: ReleaseSplit | None
    warning_impact: list[WarningImpact]
    themes: list[ThemeOut]


class RateOut(BaseModel):
    n: int
    passes: int | None
    fails: int | None
    pass_rate: int | None
    interval: Interval | None = Field(description="95% Wilson interval, whole percentages.")
    not_counted: int
    thin: bool = Field(description="n below the minimum (10): not flagged.")


class Wording(BaseModel):
    text: str
    text_hash: str
    first_seen: datetime | None
    last_seen: datetime | None
    results: int


class ReleaseRate(RateOut):
    release: int | None


class AgentRate(RateOut):
    agent: str | None


class RecentFinding(BaseModel):
    finding_id: str
    session_id: str
    started_at: datetime | None
    status: str
    reason: str | None
    evidence: list[EvidenceOut]
    pattern_id: str | None


class RuleInsights(BaseModel):
    filters: FiltersOut
    procedure_id: str
    rule_id: str
    kind: RuleKind
    index: int | None
    text: str | None
    wordings: list[Wording]
    overall: RateOut
    releases: list[ReleaseRate]
    agents: list[AgentRate]
    patterns: list[RulePatternOut]
    recent_findings: list[RecentFinding]


class CompareRule(BaseModel):
    procedure_id: str
    rule_id: str
    kind: RuleKind | None
    index: int | None
    text: str | None
    rule_change: Literal["new", "removed", "changed"] | None
    a: RateOut
    b: RateOut
    delta_points: int | None = Field(description="B minus A, in points; null unless both sides have at least min_n.")
    raw_delta_points: int | None
    flag: Literal["thin", "regressed", "improved", "steady"]


class CompareRate(BaseModel):
    n: int
    passes: int
    pass_rate: int | None
    interval: Interval | None


class CompareProcedure(BaseModel):
    procedure_id: str
    procedure_name: str
    a: CompareRate
    b: CompareRate
    delta_points: int | None
    flag: Literal["thin", "regressed", "improved", "steady"]


class CompareResponse(BaseModel):
    filters: FiltersOut
    a: int | None
    b: int | None
    releases: list[int]
    min_n: int
    flag_points: int
    procedures: list[CompareProcedure]
    rules: list[CompareRule]


class ReportHeadline(TilesValues):
    multi_problem_sessions: int


class ReportProcedure(BaseModel):
    procedure_id: str
    procedure_name: str
    themes: list[ThemeOut]


class ReportResponse(BaseModel):
    filters: FiltersOut
    headline: ReportHeadline
    procedures: list[ReportProcedure]
