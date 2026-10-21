"""A team's AI keys and models, over the deployment's defaults. Keys go in and never come back out."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseModel

ProviderId = Literal["anthropic", "openai", "gemini", "other", "sopops"]
Source = Literal["team", "default"]


class KeyState(BaseModel):
    source: Source | None = Field(description="team: this team's own key; default: the deployment's; null: none.")
    hint: str | None = Field(None, description="The last few characters of the team's key.")


class ProviderState(BaseModel):
    id: str | None = Field(description="The provider the models are from, or null when no model is set.")
    source: Source | None = Field(description="Where its key comes from: this team, the deployment, or nowhere yet.")
    hint: str | None = None
    api_base: str | None = None


class ModelState(BaseModel):
    model: str | None = Field(description="A LiteLLM model string, e.g. anthropic/claude-haiku-4-5.")
    source: Source | None


class ProviderOption(BaseModel):
    id: ProviderId
    label: str
    lists_models: bool = Field(description="Whether sopops can list this provider's models once it has a key.")
    key_url: str | None = Field(description="Where to make a key.")
    has_default_key: bool = Field(description="The deployment has a key for this provider, so a team can use it without its own.")


class AISettingsOut(BaseModel):
    typesafe: KeyState = Field(description="The TypeSafe key for Jev, which answers decisions. Required to check calls unless the deployment has a decision model.")
    provider: ProviderState
    fast: ModelState = Field(description="Routing, rule checks and pattern assignment, when Jev doesn't answer them.")
    reasoning: ModelState = Field(description="Reviews, rule splitting, pattern names and suggestions.")
    decision: ModelState = Field(description="The deployment's decision model (sopops-decision on a hosted sopops), which answers decisions when there's no TypeSafe key. Null when the team uses its own provider.")
    ready: bool = Field(description="Decisions (a TypeSafe key or the decision model) and both models are set, so calls get checked.")
    providers: list[ProviderOption]
    typesafe_key_url: str


class TypeSafeKeyIn(BaseModel):
    key: str = Field(description="A TypeSafe API key. Tried with one small Jev decision before it's saved.")


class ProviderIn(BaseModel):
    provider: ProviderId
    key: str | None = Field(None, description="The provider's API key. Leave it out to use the deployment's key for this provider.")
    api_base: str | None = Field(None, description="For other: the provider's base URL, or an OpenAI-compatible endpoint.")


class ModelChoices(BaseModel):
    models: list[str] = Field(description="The chat models the key can use, as LiteLLM model strings. Empty for other: type one.")
    suggested: dict[str, str] | None = Field(description="The recommended fast and reasoning models among them.")


class ModelsIn(BaseModel):
    fast: str = Field(description="A LiteLLM model string. Each model gets one short test call before it's saved.")
    reasoning: str


class CostRange(BaseModel):
    low: float
    high: float


class RoleCost(BaseModel):
    role: Literal["decision", "fast", "reasoning"]
    model: str | None
    input_tokens: int = Field(description="Input tokens per checked call for this role.")
    output_tokens: int
    input_price: float | None = Field(description="USD per million input tokens, or null when no price is on file.")
    output_price: float | None
    low: float | None = Field(description="USD per call, the low end; null when unpriced.")
    high: float | None


class CostEstimate(BaseModel):
    basis: Literal["yours", "typical"] = Field(description="yours: this team's average over its last 30 days of checked calls; typical: a typical call.")
    basis_calls: int | None = Field(description="How many checked calls the average covers, when it's the team's own.")
    calls_per_month: int
    per_call: CostRange = Field(description="USD per checked call, at list prices, before caching or batch discounts. Unpriced models are left out.")
    per_month: CostRange
    roles: list[RoleCost]
    unpriced: list[str] = Field(description="Models with no price on file, left out of the totals.")


ResetWhat = Literal["typesafe", "provider", "models"]
