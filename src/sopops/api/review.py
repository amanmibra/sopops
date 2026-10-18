"""Review: the queue of patterns that need a verdict, verdicts, overrides on findings, the regression export, and
calibrating check thresholds from those labels."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Query, Response

from ..errors import workspace_id
from ..jobs import Jobs
from ..pipeline.types import CalibrateRef, SuggestRef
from ..schemas import CalibrateIn, CalibrateOut, OverrideIn, OverrideOut, ReviewResponse, ReviewState, VerdictIn, VerdictSaved
from ..store import Store
from ..verdicts import SUGGESTIBLE
from .deps import OPEN


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/review", response_model=ReviewResponse, dependencies=OPEN, tags=["review"])
    def review_queue(workspace: str, limit: int = Query(50, ge=1, le=500),
                     state: list[ReviewState] | None = Query(None, description="Review states to list (repeatable); default needs_review and regressed.")) -> dict:
        """Active leaf patterns (a rule pattern that isn't split, else its failure modes) by review state, most
        sessions first: by default what needs a reviewer (`needs_review`, `regressed`); `state=open` for verdicts
        waiting for a fix, and so on. `counts` has every state."""
        return store.verdicts.queue(workspace_id(workspace), limit, state)

    @r.post("/v1/workspaces/{workspace}/patterns/{pattern_id}/verdict", response_model=VerdictSaved, dependencies=OPEN, tags=["review"])
    def set_verdict(workspace: str, pattern_id: str, req: VerdictIn, background: BackgroundTasks) -> dict:
        """Give a pattern a verdict (the history is kept; the latest counts). `acceptable` and `checker_wrong` suppress
        its findings, now and later. With `suggest`, a verdict that calls for an edit queues a suggestion."""
        ws = workspace_id(workspace)
        out = store.verdicts.set(ws, pattern_id, req.verdict, req.note, req.reviewer)
        out["suggestion"] = None
        if req.suggest and req.verdict in SUGGESTIBLE:
            s = store.suggestions.create(ws, pattern_id)
            jobs.suggest(SuggestRef(workspace_id=ws, suggestion_id=s["suggestion_id"]), background)
            out["suggestion"] = s
        return out

    @r.post("/v1/workspaces/{workspace}/findings/{finding_id}/override", response_model=OverrideOut, dependencies=OPEN, tags=["review"])
    def override_finding(workspace: str, finding_id: str, req: OverrideIn) -> dict:
        """Say whether the checker got one finding right. A wrong one is suppressed and a negative calibration label;
        it's kept by session and rule, so it outlives a re-check."""
        return store.verdicts.override(workspace_id(workspace), finding_id, req.correct, req.note, req.reviewer)

    @r.get("/v1/workspaces/{workspace}/patterns/{pattern_id}/regression", dependencies=OPEN, tags=["review"], response_class=Response,
           responses={200: {"content": {"application/x-ndjson": {}}, "description": "One JSON object per line."}})
    def export_regression(workspace: str, pattern_id: str) -> Response:
        """The pattern's sessions as regression cases, as JSON Lines: per finding, the session, its transcript
        (`transcript_ref`), the rule, and what should happen once it's fixed. `expect: agent` (a `real` mistake):
        replay the conversation against the fixed prompt; the agent should pass the rule (`expected_status`).
        `expect: checker` (acceptable, sop_wrong, checker_wrong, or a finding overridden as wrong): re-check this
        transcript; the rule should come out `expected_status`. Needs a verdict."""
        rows = store.verdicts.regression(workspace_id(workspace), pattern_id)
        body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        return Response(body, media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="regression-{pattern_id}.jsonl"'})

    @r.post("/v1/workspaces/{workspace}/calibrate", response_model=CalibrateOut, dependencies=OPEN, tags=["review"])
    def calibrate(workspace: str, req: CalibrateIn | None = None) -> dict:
        """Fit each procedure's check thresholds to the workspace's labels (verdicts and overrides), as a job, and
        return per rule the threshold before and after, and the labels used. A procedure below the minimums (5 labeled
        sessions, a rule with 3 labeled failures) is refused, with the reason. `dry_run` doesn't save."""
        req = req or CalibrateIn()
        return jobs.calibrate(CalibrateRef(workspace_id=workspace_id(workspace), procedure_id=req.procedure, dry_run=req.dry_run))

    return r
