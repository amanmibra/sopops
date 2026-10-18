"""Calibrating check thresholds from a workspace's review labels."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseModel
from .checks import RuleKind

RefusalCode = Literal["too_few_labeled_sessions", "too_few_failures", "not_in_release", "too_alike", "held_out", "other"]


class Refusal(BaseModel):
    code: RefusalCode = Field(description="Procedures: `too_few_labeled_sessions` (fewer than 5 with a reviewed finding), `too_few_failures` (no rule "
                              "with 3 judged failures), `not_in_release`. Questions: `too_few_failures` (on its rule), `too_alike` (the answers "
                              "don't spread enough to fit), `held_out` (the new value didn't hold on held-out sessions).")
    message: str = Field(description="The same, in a sentence.")
    have: int | None = Field(None, description="For the `too_few_*` codes: how many there are.")
    need: int | None = Field(None, description="For the `too_few_*` codes: how many it takes.")


class CalibrateIn(BaseModel):
    procedure: str | None = Field(None, description="Only this procedure (default: every procedure with labeled sessions).")
    dry_run: bool = Field(False, description="Fit and report, without saving.")


class CalibratedRule(BaseModel):
    rule_id: str | None = Field(description="Null for the goal question.")
    lineage_id: str | None = Field(None, description="The rule's lineage (the id its rule pattern is keyed by).")
    kind: RuleKind | None = None
    index: int | None = None
    text: str | None = Field(None, description="The rule as it reads now: its lineage's latest wording.")
    field: str = Field(description="The decision question.")
    before: float = Field(description="The threshold in use: calibrated before, else the default.")
    after: float
    skipped: Refusal | None = Field(description="Why it wasn't fitted.")


class CalibratedProcedure(BaseModel):
    procedure_id: str
    program: str
    labels: int = Field(description="Labeled sessions used.")
    negative_labels: int = Field(description="Findings judged wrong among them.")
    saved: bool
    skipped: Refusal | None = Field(description="Why the procedure was refused (below the minimums), if it was.")
    score_before: float | None
    score: float | None
    rules: list[CalibratedRule]


class CalibrateOut(BaseModel):
    workspace: str
    dry_run: bool
    procedures: list[CalibratedProcedure]
