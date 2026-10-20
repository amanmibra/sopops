"""Insights: every number from `sopops_server.insights`, with one definition of eligible sessions and rates."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ..errors import workspace_id
from ..insights import Filters
from ..schemas import (
    Channel,
    CompareResponse,
    OverviewResponse,
    ProcedureInsights,
    ReportResponse,
    RuleInsights,
    VersionsResponse,
)
from ..store import Store
from .deps import OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    def insight_filters(
        workspace: str,
        window: str | None = Query(None, description="24h, 7d, 30d, 90d or 365d, ending now (default 7d). Ignored when `start` is given."),
        start: datetime | None = Query(None, description="Window start (inclusive), on session start time."),
        end: datetime | None = Query(None, description="Window end (inclusive; default now)."),
        agent: str | None = None,
        procedure: str | None = None,
        release: list[int] | None = Query(None, description="Only sessions that ran these releases."),
        channel: Channel | None = None,
        test: str | None = Query(None, description="Only sessions that reported this A/B test."),
    ) -> Filters:
        return Filters.make(workspace_id(workspace), window, start, end, agent, procedure, release, channel, test=test)

    @r.get("/v1/workspaces/{workspace}/insights/overview", response_model=OverviewResponse, dependencies=OPEN, tags=["insights"])
    def insights_overview(f: Filters = Depends(insight_filters)) -> dict:
        """Tiles (current and prior window), the outcome trend with release markers, and the procedure scoreboard."""
        return store.insights.overview(f)

    @r.get("/v1/workspaces/{workspace}/insights/procedures/{procedure_id}", response_model=ProcedureInsights, dependencies=OPEN, tags=["insights"])
    def insights_procedure(procedure_id: str, f: Filters = Depends(insight_filters)) -> dict:
        """One procedure: tiles, the rule ladder, the release split, warning-sign impact, and patterns by theme."""
        return store.insights.procedure(f, procedure_id)

    @r.get("/v1/workspaces/{workspace}/insights/procedures/{procedure_id}/rules/{rule_id}", response_model=RuleInsights, dependencies=OPEN, tags=["insights"])
    def insights_rule(procedure_id: str, rule_id: str, f: Filters = Depends(insight_filters)) -> dict:
        """One rule: its wordings, pass rate per release and per agent, its patterns, and recent findings."""
        return store.insights.rule(f, procedure_id, rule_id)

    @r.get("/v1/workspaces/{workspace}/insights/releases/compare", response_model=CompareResponse, dependencies=OPEN, tags=["insights"])
    def insights_compare(a: int | None = Query(None, description="Baseline release (default: the one before b)."),
                         b: int | None = Query(None, description="Candidate release (default: the latest with sessions in the window)."),
                         f: Filters = Depends(insight_filters)) -> dict:
        """Per-rule and per-procedure pass rates of release B against release A, with n, 95% intervals and flags."""
        return store.insights.compare(f, a, b)

    @r.get("/v1/workspaces/{workspace}/insights/versions", response_model=VersionsResponse, dependencies=OPEN, tags=["insights"])
    def insights_versions(v: list[str] | None = Query(None, description="Version ids to compare, the baseline first (default: the most-run, oldest first; at most 5)."),
                          f: Filters = Depends(insight_filters)) -> dict:
        """A/B tests: an agent's versions (same agent, release and config) side by side on procedures, rules and
        warning signs, each against the baseline, with n, 95% intervals and flags."""
        return store.insights.versions(f, v or ())

    @r.get("/v1/workspaces/{workspace}/insights/report", response_model=ReportResponse, dependencies=OPEN, tags=["insights"])
    def insights_report(f: Filters = Depends(insight_filters)) -> dict:
        """The review report: headline numbers, then themes and their patterns, each with the rule quoted, example
        phrasings and the sessions."""
        return store.insights.report(f)

    return r
