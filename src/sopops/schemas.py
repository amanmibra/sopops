"""API request and response shapes (Pydantic). Separate from the table models in `models`, so database-only fields
never reach the API, and the OpenAPI spec (and the TypeScript client generated from it) stays stable."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel as _BaseModel, ConfigDict, Field

from . import forms

Files = dict[str, str]


class BaseModel(_BaseModel):
    # Responses always include fields that have defaults, so the spec (and generated types) mark them required.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class FilesRequest(BaseModel):
    files: Files = Field(description="Source files keyed by path relative to the sopc folder, e.g. 'instructions/brand-voice.md'. Paths under build/ are ignored.")


class IssueOut(BaseModel):
    code: str
    message: str = Field(description="sopc's message.")
    path: str = Field(description="Relative to the sopc folder; empty for folder-wide issues.")
    severity: Literal["error", "warning"]
    kind: Literal["agent", "instruction", "procedure", "group", "settings"] | None = Field(None, description="The item the issue is about, if known.")
    id: str | None = None
    field: str | None = Field(None, description="The item's field, as the /config endpoints name it (e.g. `steps`, `blocks`).")
    index: int | None = Field(None, description="For list fields (steps, never, warning_signs, blocks): which entry.")
    text: str | None = Field(None, description="The issue in plain words, for showing next to the field.")


class ValidateResponse(BaseModel):
    valid: bool
    issues: list[IssueOut]


class RenderedAgent(BaseModel):
    platform_ref: str
    hash: str
    prompt: str
    tools: list[str]
    sops: dict[str, dict[str, Any]] = Field(description="Tool-delivered SOPs (`delivery: auto | tool`) keyed by SOP id: what `get_sop` returns.")


class RenderResponse(BaseModel):
    agents: dict[str, RenderedAgent]
    lock: dict[str, Any] = Field(description="sopc's build/lock.json.")
    warnings: list[IssueOut]


class LintSource(BaseModel):
    block: str
    text: str


class LintFinding(BaseModel):
    code: str
    message: str
    sources: list[LintSource]
    agents: list[str]


class LintResponse(BaseModel):
    findings: list[LintFinding]


class WorkspaceOut(BaseModel):
    workspace: str
    current_release: int | None


class FileOut(BaseModel):
    path: str
    version: int
    deleted: bool
    created_at: datetime
    author: str | None
    note: str | None
    content: str | None = Field(None, description="Only when requested (`?content=true`, or a single file).")


class FilesResponse(BaseModel):
    workspace: str
    files: list[FileOut]


class EditRequest(BaseModel):
    changes: dict[str, str | None] = Field(description="{path: new content}, or null to delete the file.")
    author: str | None = None
    note: str | None = None


class ReplaceRequest(FilesRequest):
    author: str | None = None
    note: str | None = None


class WrittenFile(BaseModel):
    path: str
    version: int
    deleted: bool


class WriteResponse(BaseModel):
    workspace: str
    changed: list[WrittenFile] = Field(description="New file versions. Empty if nothing changed.")


class Component(BaseModel):
    kind: Literal["agent", "instruction", "sop", "config", "base"] = Field(description="`base` only in releases made before sopc v0.0.9.")
    id: str
    path: str | None
    version: int | None


class FileChange(BaseModel):
    path: str
    change: Literal["added", "edited", "deleted"]
    version: int | None = Field(description="Head version; null if deleted.")
    released_version: int | None = Field(description="Version in the current release; null if added.")


class DraftAgent(BaseModel):
    agent: str
    platform_ref: str
    status: Literal["added", "changed", "removed", "unchanged"]
    hash: str | None
    released_hash: str | None
    prompt: str | None = Field(description="The draft prompt; null if removed.")
    released_prompt: str | None = Field(description="The prompt in the current release; null if added.")
    components: list[Component]


class DraftResponse(BaseModel):
    workspace: str
    release: int | None = Field(description="The current release the draft is compared with.")
    valid: bool
    issues: list[IssueOut]
    files: list[FileChange] = Field(description="Files changed since the current release.")
    items: list[DraftItem] = Field(description="The same changes as agents, shared instructions, procedures, groups and settings.")
    agents: list[DraftAgent]
    lint: list[LintFinding] = Field([], description="`sopc lint` on the draft: duplicated or conflicting instructions. Advisory; publishing isn't blocked.")


class ReleaseRequest(BaseModel):
    author: str | None = None
    note: str | None = None


class ReleaseSummary(BaseModel):
    number: int
    created_at: datetime
    author: str | None
    note: str | None
    files: dict[str, int] = Field(description="{path: version} snapshot.")
    current: bool


class ReleaseResponse(BaseModel):
    release: ReleaseSummary
    created: bool = Field(description="False if head was already the current release.")


class PublishRequest(ReplaceRequest):
    pass


class PublishResponse(ReleaseResponse):
    changed: list[WrittenFile]


class ReleasedAgent(BaseModel):
    platform_ref: str
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str]
    components: list[Component]


class ReleaseDetail(ReleaseSummary):
    agents: dict[str, ReleasedAgent]


class AgentSummary(BaseModel):
    agent: str
    platform_ref: str
    hash: str


class AgentsResponse(BaseModel):
    workspace: str
    release: int
    agents: list[AgentSummary]


class AgentOut(BaseModel):
    agent: str = Field(description="sopc agent id.")
    platform_ref: str
    release: int
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str] = Field(description="Ids of SOPs to fetch with `get_sop` (`/sops/{sop_id}`).")
    components: list[Component] = Field(description="Each file the prompt was built from, at its version. Put this and `release` in call metadata.")


class FetchOut(BaseModel):
    id: int
    agent: str
    release: int
    hash: str
    at: datetime


# --- items (the /config endpoints) ----------------------------------------------------

class Step(BaseModel):
    text: str
    tool: str | None = Field(None, description="Exact name of a tool the agent has.")
    required: bool = Field(False, description="Steps only: the tool call must happen.")


class Saved(BaseModel):
    author: str | None = Field(None, description="Recorded with the new version.")
    note: str | None = None


class ItemMeta(BaseModel):
    version: int | None = Field(None, description="Version of the item's file at head.")
    updated_at: datetime | None = None
    updated_by: str | None = None
    file: str = Field(description="Where it lives in the sopc folder (for git export).")


class AgentFields(BaseModel):
    platform: Literal["livekit", "vapi", "elevenlabs", "retell"]
    platform_id: str = Field(description="The agent's id on its platform, e.g. LiveKit agent_name.")
    context: str = Field("", description="Text only this agent gets. It goes first in the prompt.")
    blocks: list[str] = Field([], description="Shared instructions, procedures and groups, in prompt order.")
    variables: dict[str, str] = Field({}, description="Values for {{placeholders}}; override the defaults in settings.")


class InstructionFields(BaseModel):
    text: str


class ProcedureFields(BaseModel):
    name: str
    goal: str = ""
    when: str = ""
    guidance: str = ""
    steps: list[Step] = Field(description="In order; at least one.")
    never: list[Step] = []
    warning_signs: list[Step] = []
    delivery: Literal["prompt", "auto", "tool"] = "prompt"


class GroupFields(BaseModel):
    blocks: list[str] = Field(description="Shared instructions, procedures and other groups, in order.")


class SettingsFields(BaseModel):
    variables: dict[str, str] = Field({}, description="Default values for {{placeholders}}, for every agent.")
    procedures_heading: str = Field(forms.DEFAULT_HEADING, description="Heading before the first procedure in each prompt; empty for none.")


class AgentIn(AgentFields, Saved):
    pass


class InstructionIn(InstructionFields, Saved):
    pass


class ProcedureIn(ProcedureFields, Saved):
    pass


class GroupIn(GroupFields, Saved):
    pass


class SettingsIn(SettingsFields, Saved):
    pass


class NewAgent(AgentIn):
    id: str


class NewInstruction(InstructionIn):
    id: str


class NewProcedure(ProcedureIn):
    id: str


class NewGroup(GroupIn):
    id: str


class Use(BaseModel):
    agent: str
    via: str | None = Field(description="The group the agent lists it through; null if the agent lists it directly.")


class UsedBy(BaseModel):
    used_by: list[Use] = Field([], description="Which agents use it.")


class AgentConfig(AgentFields, ItemMeta):
    id: str


class InstructionConfig(InstructionFields, UsedBy, ItemMeta):
    id: str


class ProcedureConfig(ProcedureFields, UsedBy, ItemMeta):
    id: str
    format: Literal["markdown", "yaml"] = Field(description="How the file is written. New procedures are Markdown.")


class GroupConfig(GroupFields, UsedBy, ItemMeta):
    id: str


class SettingsConfig(SettingsFields, ItemMeta):
    pass


class SettingsSaved(BaseModel):
    item: SettingsConfig
    changed: list[WrittenFile]


class ConfigResponse(BaseModel):
    workspace: str
    settings: SettingsConfig
    agents: list[AgentConfig]
    instructions: list[InstructionConfig]
    procedures: list[ProcedureConfig]
    groups: list[GroupConfig] = Field(description="In the order sopc.yaml lists them.")


class BlockRequest(Saved):
    block: str = Field(description="A shared instruction, procedure or group id.")


class AgentSaved(BaseModel):
    item: AgentConfig
    changed: list[WrittenFile]


class PreviewBlock(BaseModel):
    kind: Literal["agent", "instruction", "sop"]
    id: str


class PreviewResponse(BaseModel):
    valid: bool
    issues: list[IssueOut]
    prompt: str | None = Field(description="The prompt the agent would get with these fields; null if it doesn't compile.")
    hash: str | None
    blocks: list[PreviewBlock] = Field(description="What the prompt is built from, in order, groups expanded.")
    release: int | None
    released_prompt: str | None = Field(description="The agent's prompt in the current release, to compare with.")


class MigrateRequest(Saved):
    apply: bool = Field(False, description="Write the converted files as a draft. Without it, only the plan is returned.")


class MigrateResponse(BaseModel):
    workspace: str
    needed: bool = Field(description="False if head is already in the current format.")
    plan: str = Field(description="sopc migrate's plan: what changes in each file, and any warnings.")
    applied: bool
    changed: list[WrittenFile]


class MigrateFilesResponse(BaseModel):
    migrated: bool = Field(description="False if the files were already in the current format.")
    plan: str
    files: Files = Field(description="The converted files (the input if nothing changed).")


class ItemVersion(BaseModel):
    version: int
    deleted: bool
    created_at: datetime
    author: str | None
    note: str | None
    file: str


class DraftItem(BaseModel):
    kind: Literal["agent", "instruction", "procedure", "group", "settings"]
    id: str
    name: str
    change: Literal["added", "edited", "deleted"]
    version: int | None
    released_version: int | None
    before: dict[str, Any] | None = Field(description="Its fields in the current release; null if added.")
    after: dict[str, Any] | None = Field(description="Its fields in the draft; null if deleted.")


# --- sessions ----------------------------------------------------------------

Channel = Literal["voice", "text"]
# Where a session came from: a voice platform (LiveKit first), or a transport (OpenTelemetry, a file import, the API).
# Add a platform here (and to the UI's list) to accept it; the column is a plain string.
Platform = Literal["livekit", "elevenlabs", "vapi", "otel", "import", "api"]
SessionStatus = Literal["received", "routed", "checked", "excluded", "partial", "failed"]
# A procedure check's rolled-up result; a session's is the worst of its procedures' (see pipeline.rollup).
CheckResultValue = Literal["deviated", "partial", "followed", "not_measurable"]
RuleKind = Literal["step", "never", "warning"]
RuleStatus = Literal["done", "out_of_order", "skipped", "not_reached", "not_applicable", "violated", "ok", "present", "unheeded", "absent"]


class TurnIn(_BaseModel):
    """One turn. Extra fields (interrupted, confidence, ...) are kept as sent."""

    model_config = ConfigDict(extra="allow")

    role: str = Field(description="`agent`, `user`, `tool` or `system`. Common aliases (`assistant`, `bot`, `human`, `customer`, `function`) are mapped.")
    text: str = ""
    offset_s: float | None = Field(None, description="Seconds since the session started.")
    at: datetime | float | None = Field(None, description="When the turn happened, if `offset_s` isn't known: ISO 8601, or Unix seconds or milliseconds. Turned into `offset_s`.")


class ToolCallIn(_BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    arguments: Any = None
    result: Any = None
    is_error: bool = False
    call_id: str | None = None
    offset_s: float | None = None
    at: datetime | float | None = None


class SessionIn(_BaseModel):
    """A finished session in the canonical format. Every transport (helpers, webhooks, imports) converts into this."""

    platform: Platform
    external_id: str = Field(min_length=1, max_length=256, description="The platform's id for the session (room or job id, conversation id, call id). With `platform`, the session's unique key.")
    channel: Channel = "voice"
    agent: str | None = Field(None, max_length=256, description="The sopc agent id, or the agent's platform id (`tonys-pizza`, `livekit:tonys-pizza`).")
    release: int | None = Field(None, description="The sopops release the agent ran on, as returned at session start.")
    version_ref: str | None = Field(None, max_length=256, description="The platform's own version of the agent (ElevenLabs `version_id`, Vapi `assistantVersion`).")
    started_at: datetime | None = Field(None, description="Default: the first turn's `at`, else when sopops received it.")
    ended_at: datetime | None = None
    partial: bool = Field(False, description="The sender knows the transcript is incomplete.")
    turns: list[TurnIn] = Field(default_factory=list, description="In order.")
    tool_calls: list[ToolCallIn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionIngested(BaseModel):
    id: str
    created: bool = Field(description="A new session.")
    updated: bool = Field(description="An existing session whose content changed. Both false: an identical re-send, nothing changed.")
    status: SessionStatus
    warnings: list[str] = Field(description="E.g. a release that doesn't exist (the session is kept without it).")


class SessionSummary(BaseModel):
    id: str
    workspace: str
    agent_id: str | None = Field(description="The sopc agent, if the payload's agent matched one.")
    agent_ref: str | None = Field(description="The agent as sent.")
    channel: Channel
    platform: Platform
    external_id: str
    release_id: int | None
    version_ref: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: SessionStatus
    status_reason: str | None
    result: CheckResultValue | None = Field(description="The worst procedure result once checked: deviated, partial, followed or not_measurable.")
    turn_count: int
    tool_call_count: int
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = Field(description="When the pipeline last finished with it.")


class SessionTurn(_BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    role: Literal["agent", "user", "tool", "system"]
    text: str
    offset_s: float | None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    turn: int | None = Field(description="Index into `turns`; null for a tool call with no tool turn.")
    quote: str
    source: Literal["transcript", "tool_calls"] = "transcript"


class RuleResultOut(BaseModel):
    rule_id: str = Field(description="sopc's stable rule id.")
    kind: RuleKind
    index: int = Field(description="1-based position within its kind.")
    text: str
    status: RuleStatus
    evidence: list[EvidenceOut]
    reason: str | None
    finding: bool = Field(description="A deviation: skipped, violated, out_of_order or unheeded.")
    pattern_id: str | None = Field(None, description="For a finding: the pattern it joined (its most specific one).")
    pattern_name: str | None = None


class ProcedureCheckOut(BaseModel):
    procedure_id: str
    procedure_name: str | None
    procedure_version: int | None
    release_id: int | None
    applicable: bool = Field(description="The procedure applied to the session (false: the router found it didn't).")
    excluded_reason: str | None = Field(description="Applied, but the session ended before the procedure started or finished, so it isn't checked.")
    route_reason: str | None
    result: CheckResultValue | None = Field(description="Null unless checked.")
    goal_reached: bool | None
    goal_reason: str | None
    steps_applicable: int | None
    steps_done: int | None
    nevers_violated: int | None
    model: str | None
    prompt_version: str | None
    checked_at: datetime
    rules: list[RuleResultOut]


class SessionDetail(SessionSummary):
    turns: list[SessionTurn]
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any]
    checks: list[ProcedureCheckOut] = Field(description="Each procedure the router considered, with its rule results.")


class SessionPage(BaseModel):
    items: list[SessionSummary]
    next_cursor: str | None = Field(description="Pass as `cursor` for the next (older) page; null on the last page.")


class SessionEvent(BaseModel):
    """What `…/sessions/stream` sends, as `data:` of an SSE `session` event."""

    type: Literal["created", "updated"]
    workspace: str
    id: str
    status: SessionStatus


class IngestTokenInfo(BaseModel):
    exists: bool
    prefix: str | None = Field(description="The token's first characters, to tell tokens apart.")
    created_at: datetime | None


class IngestTokenCreated(BaseModel):
    token: str = Field(description="Shown once. Send as `Authorization: Bearer <token>` to the ingest endpoints.")
    prefix: str
    created_at: datetime


# --- patterns and insights ---------------------------------------------------------------------------------------

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
    first_seen: datetime | None
    last_seen: datetime | None
    theme_name: str | None = None
    examples: list[ExampleOut] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list, description="In the report: the most recent sessions (up to 12).")
    need_attention_pct: int | None = Field(None, description="In the report: sessions in it that need attention / all that do.")


class RuleRef(BaseModel):
    kind: RuleKind | None
    index: int | None
    text: str | None


class RulePatternOut(PatternOut):
    sub_patterns: list[PatternOut]
    rule: RuleRef | None = None


class PatternsResponse(BaseModel):
    items: list[RulePatternOut]


class PatternDetail(PatternOut):
    rule: RuleRef
    sub_patterns: list[PatternOut]


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
