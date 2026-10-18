"""SOP checks: a procedure's result and each rule's status and evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from .base import BaseModel


# A procedure check's rolled-up result; a session's is the worst of its procedures' (see pipeline.rollup).
CheckResultValue = Literal["deviated", "partial", "followed", "not_measurable"]


RuleKind = Literal["step", "never", "warning"]


RuleStatus = Literal["done", "out_of_order", "skipped", "not_reached", "not_applicable", "violated", "ok", "present", "unheeded", "absent"]


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
