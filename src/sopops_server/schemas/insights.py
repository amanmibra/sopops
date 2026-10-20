"""Insights: overview, procedure, rule, release compare and the report."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .checks import EvidenceOut, RuleKind
from .lineage import WordingRef
from .patterns import FiltersOut, Interval, RulePatternOut, ThemeOut

Tone = Literal["bad", "warn"]


class Thresholds(BaseModel):
    """The cut-offs every view applies, returned so the UI never hardcodes one."""

    min_n: int = Field(description="Below this n a rate is thin: no tone, no flag, no \"most failed\".")
    flag_points: int = Field(description="A release comparison flags a change of at least this many points (and non-overlapping 95% intervals).")
    warning_drop_points: int = Field(description="A warning sign is flagged when the pass rate drops this many points with it present.")
    hot_min_n: int = Field(description="A hot-rule callout needs this many judged results.")
    fail_warn: int = Field(description="Fail rate (%) that reads as a warning, at n >= min_n.")
    fail_bad: int = Field(description="Fail rate (%) that reads as bad, at n >= min_n.")
    agent_min_n: int = Field(description="An agent's biggest gap only counts rules it ran this many times.")
    rising_min: int = Field(description="A rising pattern has at least this many findings, and twice the prior window's.")


class TilesValues(BaseModel):
    sessions_checked: int = Field(description="Eligible sessions: checked (or incomplete) with a result.")
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
    rule_n: int = Field(description="Step and never rule results that passed or failed.")
    rule_passes: int
    rule_pass_rate: int | None = Field(description="Rule results passed / rule_n (warning signs left out).")
    never_violations: int = Field(description="Never rules broken (one session can break several).")
    never_sessions: int = Field(description="Sessions that broke a never rule.")
    suppressed: int = Field(0, description="Findings a verdict or override suppressed: left out of every number above.")


class Tiles(BaseModel):
    current: TilesValues
    prior: TilesValues
    delta_points: dict[str, int | None]
    delta_pct: dict[str, int | None]


class TrendBucket(BaseModel):
    start: datetime
    end: datetime = Field(description="The next bucket's start, or the window's end.")
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
    tone: Tone | None


class SparkPoint(BaseModel):
    start: datetime
    n: int
    pass_rate: int | None


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
    top_failing_rules: list[FailingRule] = Field(description="The two rules failing most, among rules judged at least min_n times.")
    never_violations: int
    open_patterns: int = Field(description="Patterns (each finding's most specific one) with findings in the window.")
    suppressed: int = Field(0, description="Suppressed findings in the window (not in any other count).")
    rules: int
    spark: list[SparkPoint] = Field(description="Pass rate per trend bucket: follows the selected window.")
    callouts: int = Field(description="How many of the overview's callouts are about this procedure.")


class Problem(BaseModel):
    type: Literal["skipped", "never", "out_of_order", "not_reached", "unheeded"]
    label: str
    checks: int = Field(description="Measurable procedure checks with this problem (they overlap).")
    of: int = Field(description="Measurable procedure checks.")
    pct: int | None
    kind: RuleKind
    statuses: list[str] = Field(description="The rule statuses behind it, for the sessions drill-down.")


class AgentGap(FailingRule):
    procedure_id: str


class AgentRow(BaseModel):
    agent: str | None
    sessions: int
    checks: int
    measurable: int
    followed: int
    partial: int
    deviated: int
    pass_rate: int | None
    prior_pass_rate: int | None
    prior_measurable: int
    delta_points: int | None = Field(description="Null unless both windows have at least min_n measurable checks.")
    thin: bool
    top_gap: AgentGap | None = Field(description="The rule this agent fails most, among rules it ran at least agent_min_n times.")
    median_duration_s: float | None


class CalloutDrill(BaseModel):
    """Sessions-list filters for the sessions behind a callout."""

    procedure: str | None = None
    rule: str | None = None
    rule_status: list[str] | None = None
    release: int | None = None
    pattern: str | None = None


class Callout(BaseModel):
    id: str
    kind: Literal["hot_rule", "release", "warning", "rising_pattern"]
    tone: Literal["bad", "good"]
    procedure_id: str
    procedure_name: str
    rule_id: str | None
    title: str
    body: str
    drill: CalloutDrill


class OverviewResponse(BaseModel):
    filters: FiltersOut
    thresholds: Thresholds
    tiles: Tiles
    trend: Trend
    scoreboard: list[ScoreboardRow]
    problems: list[Problem]
    agents: list[AgentRow]
    callouts: list[Callout]


class LadderRow(BaseModel):
    rule_id: str = Field(description="The rule as sessions ran it most recently; its earlier wordings are counted with it.")
    lineage_id: str = Field(description="The id of the rule's first wording (rule patterns are keyed by it).")
    kind: RuleKind
    index: int
    text: str
    wordings: int = Field(description="How many wordings the rule has had; more than 1: it was reworded.")
    wording_history: list[WordingRef] = Field(description="Every wording, oldest first, with the release that published it.")
    reworded_in: int | None = Field(description="The release that brought the current wording, when it was reworded.")
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
    suppressed: int = Field(0, description="Suppressed findings on the rule in the window (not in `findings` or the rates).")
    pattern_id: str | None
    patterns: int
    reached_pct: int | None = Field(description="Steps: judged sessions still going when the step was due (not reached left out).")
    tone: Tone | None
    most_failed: bool = Field(description="The highest fail rate among rules with n >= min_n.")


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
    thresholds: Thresholds
    procedure_id: str
    procedure_name: str
    tiles: Tiles
    trend: Trend
    rules: list[LadderRow]
    release_split: ReleaseSplit | None
    warning_impact: list[WarningImpact]
    themes: list[ThemeOut]
    problems: list[Problem]
    agents: list[AgentRow]
    callouts: list[Callout]


class RateOut(BaseModel):
    n: int
    passes: int | None
    fails: int | None
    pass_rate: int | None
    interval: Interval | None = Field(description="95% Wilson interval, whole percentages.")
    not_counted: int
    thin: bool = Field(description="n below the minimum (10): not flagged.")
    counts: dict[str, int] = Field(description="Results by status.")


class Wording(BaseModel):
    rule_id: str = Field(description="sopc's id for this wording.")
    from_release: int | None = Field(description="The release that published it (its lineage link's), else the first release a session ran it on.")
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
    thresholds: Thresholds
    procedure_id: str
    rule_id: str = Field(description="The rule as sessions ran it most recently (the path takes any of its wordings).")
    lineage_id: str
    kind: RuleKind
    index: int | None
    text: str | None
    wordings: list[Wording] = Field(description="Every wording of the rule (its lineage), most recently run first.")
    reworded_in: int | None = Field(description="The release that brought the current wording, when it was reworded.")
    overall: RateOut
    releases: list[ReleaseRate]
    agents: list[AgentRate]
    patterns: list[RulePatternOut]
    recent_findings: list[RecentFinding]


class CompareRule(BaseModel):
    procedure_id: str
    rule_id: str = Field(description="The rule as release B words it (A, if B doesn't have it).")
    lineage_id: str
    kind: RuleKind | None
    index: int | None
    text: str | None
    a_text: str | None = Field(description="Release A's wording, when B rewords the rule.")
    rule_change: Literal["new", "removed", "changed"] | None = Field(description="`changed`: B runs another wording of the rule (its lineage).")
    reworded_in: int | None = Field(None, description="When `changed`: the release that brought B's wording.")
    wording_history: list[WordingRef] = Field(default_factory=list, description="Every wording of the rule, oldest first, with the release that published it.")
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
    followed: int
    partial: int
    deviated: int
    not_measurable: int


class ReleasePoint(BaseModel):
    release: int
    sessions: int
    n: int = Field(description="Measurable procedure checks on this release.")
    passes: int
    pass_rate: int | None
    interval: Interval | None
    thin: bool


class CompareProcedure(BaseModel):
    procedure_id: str
    procedure_name: str
    a: CompareRate
    b: CompareRate
    delta_points: int | None
    flag: Literal["thin", "regressed", "improved", "steady"]


class CompareResponse(BaseModel):
    filters: FiltersOut
    thresholds: Thresholds
    a: int | None
    b: int | None
    releases: list[int]
    min_n: int
    flag_points: int
    procedures: list[CompareProcedure]
    rules: list[CompareRule]
    sessions_a: int
    sessions_b: int
    timeline: list[ReleasePoint] = Field(description="Every release with sessions in the window, by the release each session ran.")


Flag = Literal["thin", "regressed", "improved", "steady"]


class VersionOut(BaseModel):
    id: str = Field(description="Stable for the same agent, release and config.")
    label: str = Field(description="A, B, C, ... by the version's first session ever (in the test, or the agent, when one is set).")
    agent: str | None
    release: int | None = Field(description="The sopops release, i.e. the prompt, it ran.")
    config_hash: str | None = Field(description="Null: sessions that reported no config.")
    config: dict[str, str | int | float | bool] = Field(description="`llm`, `stt`, `tts`, `voice`, `prompt` (its hash), `version`, and any other setting.")
    prompt: str | None = Field(description="The prompt text, when the config reported one.")
    sessions: int = Field(description="In the window.")
    checked: int = Field(description="Eligible sessions: their checks ran.")
    first_at: datetime
    last_at: datetime


class VersionProcedureCell(BaseModel):
    version: str
    triggered: int = Field(description="Sessions this procedure applied to.")
    trigger_rate: int | None = Field(description="triggered / the version's checked sessions.")
    n: int = Field(description="Measurable checks.")
    passes: int
    pass_rate: int | None = Field(description="Followed / measurable.")
    interval: Interval | None
    followed: int
    partial: int
    deviated: int
    not_measurable: int
    goal_n: int
    goal_met: int
    goal_rate: int | None
    thin: bool
    delta_points: int | None = Field(description="Against the baseline, in points; null for the baseline or below min_n.")
    flag: Flag | None = Field(description="Pass rate against the baseline; null for the baseline.")
    goal_flag: Flag | None


class VersionProcedure(BaseModel):
    procedure_id: str
    procedure_name: str
    cells: list[VersionProcedureCell] = Field(description="One per selected version, in order.")


class VersionRuleCell(BaseModel):
    version: str
    n: int = Field(description="Steps and never rules: passes + fails. Warning signs: every result.")
    passes: int | None
    fails: int | None
    pass_rate: int | None
    interval: Interval | None
    thin: bool
    counts: dict[str, int]
    not_counted: int
    present: int | None = Field(description="Warning signs: how often it showed up.")
    present_rate: int | None
    delta_points: int | None
    flag: Flag | None


class VersionRule(BaseModel):
    procedure_id: str
    lineage_id: str
    rule_id: str
    kind: RuleKind | None
    index: int | None
    text: str | None
    cells: list[VersionRuleCell]


class ABTestOut(BaseModel):
    name: str
    sessions: int
    versions: int


class VersionsResponse(BaseModel):
    filters: FiltersOut
    thresholds: Thresholds
    max_versions: int
    tests: list[ABTestOut] = Field(description="A/B tests with sessions in the window.")
    versions: list[VersionOut] = Field(description="Every version with sessions in the window, by label.")
    selected: list[str] = Field(description="The versions compared, the baseline first.")
    baseline: str | None
    differs: list[str] = Field(description="What isn't the same across the selected versions: `agent`, `release`, then config keys.")
    untagged: int = Field(description="Sessions in the window that reported no config.")
    procedures: list[VersionProcedure]
    rules: list[VersionRule]


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
