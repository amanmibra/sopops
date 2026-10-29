"""SOP checks: a procedure's result and each rule's status and evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from .base import BaseModel

# A procedure check's rolled-up result; a session's is the worst of its procedures' (see pipeline.rollup).
CheckResultValue = Literal["deviated", "partial", "followed", "not_measurable"]


RuleKind = Literal["step", "option", "never", "warning"]  # option: one of a step's OR options


RuleStatus = Literal["done", "out_of_order", "skipped", "not_reached", "not_applicable", "violated", "ok", "present", "unheeded", "absent"]


class EvidenceOut(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    turn: int | None = Field(description="Index into `turns`; null for a tool call with no tool turn.")
    quote: str
    source: Literal["transcript", "tool_calls"] = "transcript"


class RuleCorrectionOut(BaseModel):
    status: RuleStatus = Field(description="What the reviewer says actually happened.")
    checked_status: RuleStatus | None = Field(description="What the checker said when they did (null for an override).")
    source: Literal["correction", "spot_check", "override"]
    note: str | None
    reviewer: str | None
    created_at: datetime


class RuleResultOut(BaseModel):
    rule_id: str = Field(description="sop's stable rule id.")
    kind: RuleKind
    index: int = Field(description="1-based position within its kind; an option has its step's.")
    option: int | None = Field(None, description="An option: its 1-based place among its step's OR options.")
    option_of: str | None = Field(None, description="An option: its step's rule id.")
    text: str
    status: RuleStatus
    evidence: list[EvidenceOut]
    reason: str | None
    finding: bool = Field(description="A deviation: skipped, violated, out_of_order or unheeded.")
    pattern_id: str | None = Field(None, description="For a finding: the pattern it joined (its most specific one).")
    pattern_name: str | None = None
    finding_id: str | None = Field(None, description="For a finding: its id (to override it).")
    suppressed: bool = Field(False, description="A finding a verdict or override says isn't a problem; the result leaves it out.")
    override: bool | None = Field(None, description="A reviewer's call on the finding: true (the checker was right), false, or none.")
    correction: RuleCorrectionOut | None = Field(None, description="The status a reviewer gave this rule, if the latest call gave one.")



class ProcedureCheckOut(BaseModel):
    procedure_id: str
    procedure_name: str | None
    procedure_version: int | None
    release_id: int | None
    applicable: bool = Field(description="The procedure applied to the session (false: the router found it didn't).")
    excluded_reason: str | None = Field(description="Applied, but the session ended before the procedure started or finished, so it isn't checked.")
    route_reason: str | None
    result: CheckResultValue | None = Field(description="Null unless checked. Suppressed findings are left out.")
    checked_result: CheckResultValue | None = Field(None, description="The checker's own result, with every finding.")
    goal_reached: bool | None
    goal_reason: str | None
    steps_applicable: int | None
    steps_done: int | None
    nevers_violated: int | None
    model: str | None
    prompt_version: str | None
    checked_at: datetime
    rules: list[RuleResultOut]
