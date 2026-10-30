"""Pausing a workspace's analysis, and the tags it skips (`sopops_server.analysis`): sessions keep coming in, and those
aren't checked."""

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
        """Whether the workspace's analysis is paused, since when and by whom, and the tags whose sessions it skips."""
        return store.analysis.state(workspace_id(workspace))

    @r.put(WS, response_model=AnalysisState)
    def set_analysis(workspace: str, req: AnalysisIn, background: BackgroundTasks) -> dict:
        """Pause analysis: sessions still come in and show their transcripts, but aren't routed or checked, and nothing
        is reclustered, suggested or calibrated. Resume it: the sessions held meanwhile are queued to be checked
        (`queued`). `skip_tags`: sessions with any of them come in but aren't checked;
        taking a tag off queues the sessions it skipped. Either field left out is unchanged."""
        ws = workspace_id(workspace)
        queued: list = []  # while paused, these are held like any other session, and resuming queues them again
        if req.skip_tags is not None:
            _, queued = store.analysis.set_skip_tags(ws, req.skip_tags)
        if req.paused:
            store.analysis.pause(ws)
        elif req.paused is False:
            _, held = store.analysis.resume(ws)
            queued = list({r.session_id: r for r in [*queued, *held]}.values())
        for ref in queued:
            jobs.process_session(ref, background)
        return {**store.analysis.state(ws), "queued": len(queued)}

    return r
