"""Review: the queue of patterns that need a verdict, verdicts, overrides on findings, and the regression export."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Query, Response

from ..errors import workspace_id
from ..jobs import Jobs
from ..pipeline.types import SuggestRef
from ..schemas import OverrideIn, OverrideOut, ReviewResponse, VerdictIn, VerdictSaved
from ..store import Store
from ..verdicts import SUGGESTIBLE
from .deps import OPEN


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/review", response_model=ReviewResponse, dependencies=OPEN, tags=["review"])
    def review_queue(workspace: str, limit: int = Query(50, ge=1, le=500)) -> dict:
        """Patterns that need a verdict: no verdict yet, or findings that aren't suppressed since it. Each active leaf
        pattern (a rule pattern that isn't split, else its failure modes), most sessions first."""
        return store.verdicts.queue(workspace_id(workspace), limit)

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

    return r
