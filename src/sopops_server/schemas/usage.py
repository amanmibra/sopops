"""Model spend: tokens and estimated cost of model calls, by purpose, model and day."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel

Purpose = Literal["route", "check", "review", "cluster", "theme", "suggestion", "calibrate", "onboarding"]


class SpendTotals(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float = Field(description="Estimated from the price table in use (USD); calls to a model without a price add nothing.")
    unpriced_calls: int = Field(description="Calls to a model with no price: their tokens count, their cost doesn't.")


class PurposeSpend(SpendTotals):
    purpose: Purpose


class ModelSpend(SpendTotals):
    model: str


class DaySpend(SpendTotals):
    day: date = Field(description="A UTC day.")


class UsageResponse(BaseModel):
    start: datetime
    end: datetime
    total: SpendTotals
    by_purpose: list[PurposeSpend] = Field(description="route, check, review (the reviewer on flagged rules), cluster (assigning findings, splitting and naming "
                                           "patterns), theme, suggestion, calibrate, onboarding. Largest cost first.")
    by_model: list[ModelSpend]
    sessions: int = Field(description="Sessions with a model call about them in the window.")
    per_session_usd: float | None = Field(description="The cost of calls about a session (routing, checks, review, assigning its findings), per session.")
    daily: list[DaySpend] = Field(description="Every UTC day of the window, empty ones included.")


class ModelPrice(BaseModel):
    model: str = Field(description="Matches a model string without its provider, or as a prefix (a dated snapshot).")
    input_per_m: float = Field(description="USD per million input tokens.")
    output_per_m: float = Field(description="USD per million output tokens.")
    source: Literal["default", "env"]


class RolePrice(BaseModel):
    role: Literal["fast", "reasoning", "decide"]
    model: str | None = Field(description="Null when no model is configured for it.")
    input_per_m: float | None = Field(description="Null when the model has no price.")
    output_per_m: float | None


class PricesResponse(BaseModel):
    prices: list[ModelPrice]
    roles: list[RolePrice] = Field(description="The models in use, and the price each gets.")
    env: str = Field(description="The variable that overrides or extends the table: JSON, {\"model\": [input, output]}.")


class FeaturesOut(BaseModel):
    features: list[str] = Field(description="Features this server adds beyond open-source sopops, turned on by its extensions: `fine_tune` on hosted sopops.")
