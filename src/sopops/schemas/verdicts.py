"""Verdicts on patterns and overrides on findings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel

VerdictKind = Literal["real", "acceptable", "sop_wrong", "checker_wrong"]
ReviewState = Literal["needs_review", "open", "fixing", "fixed", "regressed", "closed"]
ReviewReason = Literal["no_verdict", "carried_over", "reclustered", "reworded", "came_back"]
REVIEW_REASON_HELP = ("Why it needs review: `no_verdict`; `carried_over` (by a recluster); `reclustered` (its findings changed since the verdict); "
                      "`reworded` (the rule was reworded after an acceptable or checker_wrong verdict); `came_back` (regressed). Null otherwise.")
REVIEW_STATE_HELP = ("`needs_review`: no verdict, a verdict carried over by a recluster, or one older than a recluster that changed its findings. "
                     "`open`: real or sop_wrong with no suggestion applied. `fixing`: a suggestion applied (draft or pull request), waiting for its "
                     "release to be live and for enough sessions on it. `fixed`: no findings in 10 sessions on that release or later. `regressed`: "
                     "findings came back on such sessions. `closed`: acceptable or checker_wrong.")


class VerdictIn(BaseModel):
    verdict: VerdictKind = Field(description="`real`: a real agent mistake (keeps counting). `acceptable`: fine in this situation (its findings are suppressed). "
                                 "`sop_wrong`: the instruction is wrong or outdated. `checker_wrong`: sopops misjudged (suppressed, and a negative calibration label).")
    note: str | None = None
    reviewer: str | None = Field(None, description="Who gave it (free text until there's auth).")
    suggest: bool = Field(True, description="Generate a suggested edit when the verdict calls for one (real, acceptable, sop_wrong).")


class VerdictOut(BaseModel):
    verdict_id: str
    pattern_id: str = Field(description="The pattern it was given on: the rule pattern's, when `inherited`.")
    verdict: VerdictKind
    note: str | None
    reviewer: str | None
    carried_from: str | None = Field(description="The retired pattern a recluster carried it over from.")
    inherited: bool = Field(False, description="A sub-pattern with no verdict of its own has its rule pattern's.")
    created_at: datetime


class OverrideIn(BaseModel):
    correct: bool = Field(description="Whether the checker got this finding right. False suppresses it and makes it a negative calibration label.")
    note: str | None = None
    reviewer: str | None = None


class OverrideOut(BaseModel):
    override_id: str
    finding_id: str
    session_id: str
    procedure_id: str
    rule_id: str
    correct: bool
    note: str | None
    reviewer: str | None
    created_at: datetime
    suppressed: bool | None = Field(description="Whether the finding is suppressed now (an `acceptable` pattern suppresses it either way).")
