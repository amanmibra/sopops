"""A team's AI keys and models, over the deployment's defaults. Keys go in and never come back out."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseModel

ProviderId = Literal["anthropic", "openai", "gemini", "other"]
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
    typesafe: KeyState = Field(description="The TypeSafe key for Jev, which answers every decision. Required to check calls.")
    provider: ProviderState
    fast: ModelState = Field(description="Routing, rule checks and pattern assignment, when Jev doesn't answer them.")
    reasoning: ModelState = Field(description="Reviews, rule splitting, pattern names and suggestions.")
    ready: bool = Field(description="A TypeSafe key and both models are set, so calls get checked.")
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


ResetWhat = Literal["typesafe", "provider", "models"]
