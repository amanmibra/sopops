"""Suggested procedure edits."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .verdicts import VerdictOut

SuggestionStatus = Literal["generating", "proposed", "applied", "dismissed", "pr_open", "merged", "failed"]
Freshness = Literal["current", "merges", "conflict"]


class SuggestionSummary(BaseModel):
    suggestion_id: str
    pattern_id: str
    guidance: str | None = Field(None, description="What the reviewer asked the fix to do, in their words.")
    pattern_name: str | None
    procedure_id: str
    rule_id: str
    block_id: str = Field(description="The block the edit is to: the procedure.")
    status: SuggestionStatus = Field(description="`generating` until the model and sop are done; then `proposed` or `failed`. "
                                     "A proposal becomes `applied` (a draft edit), `pr_open` (a pull request; then `merged`, or `dismissed` when it's closed) or `dismissed`.")
    rationale: str | None
    error: str | None = Field(description="Why it failed: sop's issues with the last proposal, or the model's error.")
    pr_url: str | None
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None = Field(None, description="When it was saved as a draft edit.")
    pr_opened_at: datetime | None = Field(None, description="When its pull request was opened.")
    merged_at: datetime | None = Field(None, description="When sopops saw its pull request merged (checked every 15 minutes, and when it's read).")
    released_in: int | None = Field(None, description="The first release whose file is the suggested text (for a repo: after the merge is pulled and published).")
    released_at: datetime | None = Field(None, description="When that release was published.")
    freshness: Freshness | None = Field(None, description="A proposed suggestion against the procedure's file now: `current` (unchanged since it was written), "
                                        "`merges` (changed; the edit merges in and the result compiles) or `conflict` (the same lines were changed, or the "
                                        "merged file doesn't compile: apply is refused; rewrite it). None for any other status.")


class AgentPromptChange(BaseModel):
    agent: str
    added: int = Field(description="Lines added to the agent's instructions.")
    removed: int = Field(description="Lines removed from the agent's instructions.")
    diff: str = Field(description="A unified diff of the agent's full instructions, now against suggested.")


class SuggestionPlan(BaseModel):
    agents: list[AgentPromptChange] = Field(description="Agents whose instructions change.")


class SuggestionPattern(BaseModel):
    pattern_id: str
    kind: Literal["rule", "sub", "other"]
    name: str
    description: str | None
    procedure_id: str
    rule_id: str


class MergeConflict(BaseModel):
    line: int = Field(description="Where the part starts in the file now, 1-based.")
    base: str = Field(description="The lines as they were when the suggestion was written (empty where both sides added lines).")
    current: str = Field(description="The lines now.")
    suggested: str = Field(description="The lines as the suggestion has them.")


class SinceWritten(BaseModel):
    state: Freshness
    head_version: int | None = Field(description="The file's version now (None if the file is gone).")
    changes: str | None = Field(description="A unified diff of the file, as written against to now (None when unchanged).")
    merged_text: str | None = Field(description="`merges`: the file applying would write, the edit merged into the file now.")
    conflicts: list[MergeConflict] = Field(description="`conflict`: the parts both changed, each side as it reads.")
    issues: str | None = Field(description="`conflict`: sop's errors with the merged file, or why there's nothing to merge into.")


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
    merged_into_version: int | None = Field(None, description="Applied into this version of the file, merged with changes made since it was written.")
    rewrite_of: str | None = Field(None, description="The suggestion this one rewrites against the current text.")
    rewritten_as: str | None = Field(None, description="The suggestion that rewrote this one.")
    since_written: SinceWritten | None = Field(None, description="A proposed suggestion: how the procedure's file changed since it was written, and how the edit fits it now.")


class SuggestionsResponse(BaseModel):
    items: list[SuggestionSummary]


class ApplyIn(BaseModel):
    reviewer: str | None = None


GUIDANCE = Field(None, max_length=2000, description="What the fix should do, in the reviewer's words. Sent to the model with the verdict; empty: the verdict alone.")


class SuggestIn(BaseModel):
    guidance: str | None = GUIDANCE


class RewriteIn(BaseModel):
    reviewer: str | None = None
    guidance: str | None = Field(None, max_length=2000, description="What the rewrite should do; empty keeps the guidance the suggestion was written with.")


class DismissIn(BaseModel):
    note: str | None = None
    reviewer: str | None = None
