"""Pipeline inputs and outputs."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from .rollup import Judged

# Why a session is partial: it stays partial through the pipeline (it's still checked).
SENDER_PARTIAL = "The sender marked the transcript incomplete."
IDLE_PARTIAL = "No events for a while; closed as incomplete."
PARTIAL_REASONS = (SENDER_PARTIAL, IDLE_PARTIAL)


class SessionRef(BaseModel):
    workspace_id: str
    session_id: str
    revision: str = Field(description="The session's payload hash when this run was queued. A run whose revision is stale does nothing.")


class ProcessResult(BaseModel):
    session_id: str
    status: str
    turn_count: int
    skipped: bool = Field(False, description="The session was re-sent since this run was queued, so a newer run handles it.")
    result: str | None = Field(None, description="The worst procedure result, once checked.")
    reason: str | None = None


class RouteRef(SessionRef):
    model_key: str = Field("", description="The model routing uses; Hatchet rate-limits per model key.")
    rate_limit: int = Field(60, description="Calls per minute allowed per model key.")


class RouteResult(BaseModel):
    session_id: str
    go: bool = Field(description="False: stop here (stale, nothing to check, or no model configured).")
    status: str
    release_id: int | None = None
    to_check: list[str] = Field(default_factory=list, description="Procedure ids to check, in order.")
    reason: str | None = None


class CheckRef(SessionRef):
    procedure_id: str
    model_key: str = ""
    rate_limit: int = 60


class CheckResult(BaseModel):
    session_id: str
    procedure_id: str
    result: str | None
    stored: bool


class ReclusterRef(BaseModel):
    workspace_id: str
    procedure_id: str
    rule_id: str | None = Field(None, description="Recluster this rule (if due, or `force`); none: only regroup the procedure's themes if due.")
    force: bool = False


class SuggestRef(BaseModel):
    workspace_id: str
    suggestion_id: str
    model_key: str = ""
    rate_limit: int = 60


class CalibrateRef(BaseModel):
    workspace_id: str
    procedure_id: str | None = Field(None, description="Only this procedure; default: every procedure with labeled sessions.")
    dry_run: bool = False


EXCLUDED_REASON = {
    "ended_before_start": "The session ended before the procedure started.",
    "ended_before_finish": "The session ended before the procedure finished.",
}


@dataclass
class Routed:
    """Whether one procedure applied to a session."""

    procedure_id: str
    status: str  # applied | not_applicable | ended_before_start | ended_before_finish
    reason: str

    @property
    def applicable(self) -> bool:
        return self.status != "not_applicable"

    @property
    def excluded_reason(self) -> str | None:
        return EXCLUDED_REASON.get(self.status)


@dataclass
class Checked:
    """Every rule's status for one procedure, and the goal."""

    rules: list[Judged]
    goal_reached: bool | None
    goal_reason: str
