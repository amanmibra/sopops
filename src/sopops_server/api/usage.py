"""Model spend: what model calls cost, by purpose, model and day, and the price table it's estimated with. And how
many calls a workspace sent in a period."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from ..errors import workspace_id
from ..insights import MAX_WINDOW, BadFilter, Filters
from ..schemas import CallsResponse, FeaturesOut, PricesResponse, UsageResponse
from ..store import Store
from ..usage import price_table
from .deps import OPEN


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _month_start(at: datetime) -> datetime:
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(at: datetime) -> datetime:
    """The start of the UTC month after the one `at` is in."""
    m = _month_start(at)
    return m.replace(year=m.year + 1, month=1) if m.month == 12 else m.replace(month=m.month + 1)


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/usage", response_model=UsageResponse, dependencies=OPEN, tags=["usage"])
    def model_usage(workspace: str, window: str | None = Query(None, description="24h, 7d, 30d, 90d or 365d, ending now (default 7d). Ignored when `start` is given."),
                    start: datetime | None = None, end: datetime | None = None) -> dict:
        """Model calls in the window, by when they were made: totals, by purpose and by model, the cost per session, and a
        series per UTC day. Costs are estimates from the price table (`GET /v1/model-prices`); prompts aren't stored."""
        return store.usage.window(Filters.make(workspace_id(workspace), window, start, end))

    @r.get("/v1/workspaces/{workspace}/usage/calls", response_model=CallsResponse, dependencies=OPEN, tags=["usage"])
    def call_usage(workspace: str, start: datetime | None = Query(None, description="Default: the start of this UTC month."),
                   end: datetime | None = Query(None, description="Exclusive. Default: the start of the UTC month after `start`'s.")) -> dict:
        """Calls in a period: sessions received in full (posted whole, or streamed and closed) that started in
        [start, end), in total and per UTC day. At most 366 days."""
        s = _utc(start) if start else _month_start(datetime.now(timezone.utc))
        e = _utc(end) if end else _next_month(s)
        if e <= s:
            raise BadFilter("end must be after start")
        if e - s > MAX_WINDOW:
            raise BadFilter("the period can be at most 366 days")
        return store.usage.calls(workspace_id(workspace), s, e)

    @r.get("/v1/model-prices", response_model=PricesResponse, dependencies=OPEN, tags=["usage"])
    def model_prices(workspace: str | None = Query(None, description="The team, whose own models apply over the deployment's.")) -> dict:
        """The price table in use (defaults, and SOPOPS_MODEL_PRICES), and the price each configured model gets."""
        return price_table(store.ai_keys.ai(workspace_id(workspace)) if workspace else None)

    @r.get("/v1/features", response_model=FeaturesOut, tags=["usage"])
    def server_features(request: Request) -> dict:
        """Features this server adds beyond open-source sopops, from its extensions (`fine_tune` on hosted sopops)."""
        return {"features": sorted(getattr(request.app.state, "sopops_features", set()))}

    return r
