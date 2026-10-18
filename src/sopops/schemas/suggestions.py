"""Suggested procedure edits."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .verdicts import VerdictOut

SuggestionStatus = Literal["generating", "proposed", "applied", "dismissed", "pr_open", "merged", "failed"]


class SuggestionSummary(BaseModel):
    suggestion_id: str
    pattern_id: str
    pattern_name: str | None
    procedure_id: str
    rule_id: str
    block_id: str = Field(description="The block the edit is to: the procedure.")
    status: SuggestionStatus = Field(description="`generating` until the model and sopc are done; then `proposed` or `failed`. "
                                     "A proposal becomes `applied` (a draft edit), `pr_open` (a pull request; then `merged`, or `dismissed` when it's closed) or `dismissed`.")
    rationale: str | None
    error: str | None = Field(description="Why it failed: sopc's issues with the last proposal, or the model's error.")
    pr_url: str | None
    created_at: datetime
    updated_at: datetime


class AgentPromptChange(BaseModel):
    agent: str
    added: int = Field(description="Prompt lines added.")
    removed: int = Field(description="Prompt lines removed.")
    diff: str = Field(description="A unified diff of the agent's compiled prompt, now against suggested.")


class SuggestionPlan(BaseModel):
    agents: list[AgentPromptChange] = Field(description="Agents whose prompt changes.")


class SuggestionPattern(BaseModel):
    pattern_id: str
    kind: Literal["rule", "sub", "other"]
    name: str
    description: str | None
    procedure_id: str
    rule_id: str


class SuggestionDetail(SuggestionSummary):
    path: str | None = Field(description="The procedure's file.")
    base_version: int | None = Field(description="The file's version it was made from; applying needs head to still be it.")
    old_text: str | None
    new_text: str | None
    plan: SuggestionPlan | None
    note: str | None = Field(description="Why it was dismissed.")
    reviewer: str | None
    attempts: int = Field(description="Model proposals made (a proposal that doesn't compile is sent back once).")
    model: str | None
    prompt_version: str | None
    pattern: SuggestionPattern | None
    verdict: VerdictOut | None = Field(description="The verdict it was made for.")


class SuggestionsResponse(BaseModel):
    items: list[SuggestionSummary]


class ApplyIn(BaseModel):
    reviewer: str | None = None


class DismissIn(BaseModel):
    note: str | None = None
    reviewer: str | None = None
