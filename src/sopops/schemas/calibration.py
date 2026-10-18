"""Calibrating check thresholds from a workspace's review labels."""

from __future__ import annotations

from pydantic import Field

from .base import BaseModel


class CalibrateIn(BaseModel):
    procedure: str | None = Field(None, description="Only this procedure (default: every procedure with labeled sessions).")
    dry_run: bool = Field(False, description="Fit and report, without saving.")


class CalibratedRule(BaseModel):
    rule_id: str | None = Field(description="Null for the goal question.")
    field: str = Field(description="The decision question.")
    before: float = Field(description="The threshold in use: calibrated before, else the default.")
    after: float
    skipped: str | None = Field(description="Why it wasn't fitted.")


class CalibratedProcedure(BaseModel):
    procedure_id: str
    program: str
    labels: int = Field(description="Labeled sessions used.")
    negative_labels: int = Field(description="Findings judged wrong among them.")
    saved: bool
    skipped: str | None = Field(description="Why the procedure was refused (below the minimums), if it was.")
    score_before: float | None
    score: float | None
    rules: list[CalibratedRule]


class CalibrateOut(BaseModel):
    workspace: str
    dry_run: bool
    procedures: list[CalibratedProcedure]
