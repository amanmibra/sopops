"""Model spend: what model calls cost, by purpose, model and day, and the price table it's estimated with."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from ..errors import workspace_id
from ..insights import Filters
from ..schemas import PricesResponse, UsageResponse
from ..store import Store
from ..usage import price_table
from .deps import OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/usage", response_model=UsageResponse, dependencies=OPEN, tags=["usage"])
    def model_usage(workspace: str, window: str | None = Query(None, description="24h, 7d, 30d, 90d or 365d, ending now (default 7d). Ignored when `start` is given."),
                    start: datetime | None = None, end: datetime | None = None) -> dict:
        """Model calls in the window, by when they were made: totals, by purpose and by model, the cost per session, and a
        series per UTC day. Costs are estimates from the price table (`GET /v1/model-prices`); prompts aren't stored."""
        return store.usage.window(Filters.make(workspace_id(workspace), window, start, end))

    @r.get("/v1/model-prices", response_model=PricesResponse, dependencies=OPEN, tags=["usage"])
    def model_prices() -> dict:
        """The price table in use (defaults, and SOPOPS_MODEL_PRICES), and the price each configured model gets."""
        return price_table()

    return r
