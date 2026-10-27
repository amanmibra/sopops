"""Pausing a workspace's analysis (`sopops_server.analysis`): sessions keep coming in, nothing is checked."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ..errors import workspace_id
from ..jobs import Jobs
from ..schemas import AnalysisIn, AnalysisState
from ..store import Store

WS = "/v1/workspaces/{workspace}/analysis"


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter(tags=["ai"])

    @r.get(WS, response_model=AnalysisState)
    def get_analysis(workspace: str) -> dict:
        """Whether the workspace's analysis is paused, since when and by whom."""
        return store.analysis.state(workspace_id(workspace))

    @r.put(WS, response_model=AnalysisState)
    def set_analysis(workspace: str, req: AnalysisIn, background: BackgroundTasks) -> dict:
        """Pause analysis: sessions still come in and show their transcripts, but aren't routed or checked, and nothing
        is reclustered, suggested or calibrated. Resume it: the sessions held meanwhile are queued to be checked
        (`queued`)."""
        ws = workspace_id(workspace)
        if req.paused:
            return store.analysis.pause(ws)
        state, held = store.analysis.resume(ws)
        for ref in held:
            jobs.process_session(ref, background)
        return {**state, "queued": len(held)}

    return r
