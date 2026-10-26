"""Verdicts on patterns, overrides on findings, and labels on rules: corrections and spot checks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .checks import EvidenceOut, RuleKind, RuleStatus

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
    status: RuleStatus | None = Field(None, description="What actually happened, if you say: one of the rule kind's statuses. A finding status "
                                      "(skipped, violated, out_of_order, unheeded) when `correct`, another one when not. A calibration label.")
    note: str | None = None
    reviewer: str | None = None


class OverrideOut(BaseModel):
    override_id: str
    finding_id: str
    session_id: str
    procedure_id: str
    rule_id: str
    correct: bool
    status: RuleStatus | None = None
    note: str | None
    reviewer: str | None
    created_at: datetime
    suppressed: bool | None = Field(description="Whether the finding is suppressed now (an `acceptable` pattern suppresses it either way).")


LabelSource = Literal["correction", "spot_check"]


class RuleLabelIn(BaseModel):
    status: RuleStatus = Field(description="What actually happened: one of the rule kind's statuses (steps: done, out_of_order, skipped, not_reached, "
                               "not_applicable; never rules: violated, ok; warning signs: present, unheeded, absent).")
    note: str | None = None
    reviewer: str | None = None
    source: LabelSource = Field("correction", description="`correction` (from the session) or `spot_check` (an answer in the spot-check queue).")


class RuleLabelOut(BaseModel):
    label_id: str
    session_id: str
    procedure_id: str
    rule_id: str
    kind: RuleKind
    checked_status: RuleStatus = Field(description="What the checker said.")
    status: RuleStatus
    source: LabelSource
    note: str | None
    reviewer: str | None
    created_at: datetime
    missed: bool = Field(description="A failure the checker didn't flag. Stored and shown; it doesn't add a finding.")
    suppressed: bool | None = Field(description="For a rule with a finding: whether it's suppressed now. Null without one.")


class SpotCheckItem(BaseModel):
    spot_check_id: str
    session_id: str
    started_at: datetime
    agent_id: str | None
    procedure_id: str
    procedure_name: str | None
    rule_id: str
    kind: RuleKind
    index: int
    text: str
    status: RuleStatus = Field(description="What the checker said.")
    evidence: list[EvidenceOut]
    reason: str | None
    decision_p: float | None = Field(description="The deciding probability nearest its threshold; null in review mode.")
    decision_threshold: float | None
    decided_status: RuleStatus | None = Field(description="The decision's status, before any review.")
    reviewed: bool | None = Field(description="Whether the reasoning model reviewed the rule.")
    weight: float = Field(description="Its value when drawn: close calls and changed decisions weigh more.")
    why: list[SpotCheckWhy]
    statuses: list[RuleStatus] = Field(description="The statuses this rule can have.")
    answered: bool
    answer: RuleStatus | None = Field(description="The status the answer gave.")


SpotCheckWhy = Literal["close_call", "review_changed", "sample"]


class SpotCheckResponse(BaseModel):
    batch_id: str | None = Field(description="The current batch; null before the first is drawn.")
    drawn_at: datetime | None
    size: int = Field(description="Items in the batch, less those that expired unanswered (after 7 days).")
    answered: int
    corrections: int = Field(description="Answers that said the checker was wrong.")
    done: bool = Field(description="Every item answered: draw the next batch with `POST …/spot-checks/batches`.")
    items: list[SpotCheckItem] = Field(description="The batch's items, answered ones too, filtered and sorted as asked.")
    default_batch: int = Field(description="The workspace's batch size.")


class SpotCheckDrawIn(BaseModel):
    size: int | None = Field(None, ge=1, le=200, description="How many to draw; default the workspace's batch size.")


class SpotCheckSettings(BaseModel):
    batch: int = Field(description="How many rule results a spot-check batch draws.")
    default: bool = Field(description="The size is sopops' default (25).")


class SpotCheckSettingsIn(BaseModel):
    batch: int | None = Field(description="1 to 200; null goes back to the default.", ge=1, le=200)
