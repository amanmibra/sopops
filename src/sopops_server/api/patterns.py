"""Patterns and themes: findings grouped by rule and failure mode, counted in a window; reclustering on demand."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ..errors import NotFound, workspace_id
from ..insights import Filters
from ..jobs import Jobs
from ..schemas import (
    Channel,
    PatternDetail,
    PatternsResponse,
    ReclusterRequest,
    ReclusterResponse,
)
from ..store import Store
from .deps import OPEN


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()

    def pattern_filters(workspace: str, window: str | None = Query(None, description="Default 365d."), start: datetime | None = None, end: datetime | None = None,
                        agent: str | None = None, release: list[int] | None = Query(None), channel: Channel | None = None) -> Filters:
        return Filters.make(workspace_id(workspace), window or "365d", start, end, agent, None, release, channel)

    @r.get("/v1/workspaces/{workspace}/patterns", response_model=PatternsResponse, dependencies=OPEN, tags=["patterns"])
    def list_patterns(workspace: str, procedure: str | None = None, rule: str | None = None, f: Filters = Depends(pattern_filters)) -> dict:
        """Rule patterns (every finding on a rule) with their named failure modes, counted in the window, each with
        its verdict and review state; `clustered_at`: the workspace's last clustering."""
        out = store.insights.patterns(f, procedure, rule)
        store.verdicts.annotate(f.workspace, [x for p in out["items"] for x in (p, *p["sub_patterns"])])
        return {**out, "clustered_at": store.verdicts.clustered_at(f.workspace)}

    @r.get("/v1/workspaces/{workspace}/patterns/{pattern_id}", response_model=PatternDetail, dependencies=OPEN, tags=["patterns"])
    def get_pattern(workspace: str, pattern_id: str, f: Filters = Depends(pattern_filters)) -> dict:
        """One pattern: the rule it's about, example phrasings and its sessions in the window, its verdict (with its
        history), suppressed findings and suggestions."""
        out = store.insights.pattern(f, pattern_id)
        if out is None:
            raise NotFound(f"no pattern '{pattern_id}'")
        return {**out, **store.verdicts.for_pattern(f.workspace, pattern_id)}

    @r.post("/v1/workspaces/{workspace}/patterns/recluster", response_model=ReclusterResponse, dependencies=OPEN, tags=["patterns"])
    def recluster_patterns(workspace: str, req: ReclusterRequest | None = None) -> dict:
        """Assign any unassigned findings, then recluster rules (those due, or all with `force`) and regroup themes.
        Runs here and now (it calls the models); the daily cron does the same for every workspace."""
        req = req or ReclusterRequest()
        ws = workspace_id(workspace)
        pats = store.patterns
        out = pats.assign_pending(ws, jobs.ai)
        if req.rule and req.procedure:
            done = pats.recluster(ws, req.procedure, req.rule, jobs.ai, force=req.force)
            out.reclustered += [req.rule] if done else []
            if pats.regroup_themes(ws, req.procedure, jobs.ai, force=done):
                out.themed.append(req.procedure)
        else:
            more = pats.recluster_due(ws, jobs.ai) if not req.force else pats.recluster_all(ws, req.procedure, jobs.ai)
            out.reclustered += more.reclustered
            out.themed += more.themed
        return {"assigned": out.assigned, "reclustered": sorted(set(out.reclustered)), "themed": sorted(set(out.themed))}

    return r
