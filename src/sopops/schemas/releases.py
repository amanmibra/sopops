"""Drafts, releases, agents and fetches."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .config import DraftItem
from .files import Component, IssueOut, LintFinding, ReplaceRequest, WrittenFile


class FileChange(BaseModel):
    path: str
    change: Literal["added", "edited", "deleted"]
    version: int | None = Field(description="Head version; null if deleted.")
    released_version: int | None = Field(description="Version in the current release; null if added.")


class DraftAgent(BaseModel):
    agent: str
    platform_ref: str
    status: Literal["added", "changed", "removed", "unchanged"]
    hash: str | None
    released_hash: str | None
    prompt: str | None = Field(description="The draft prompt; null if removed.")
    released_prompt: str | None = Field(description="The prompt in the current release; null if added.")
    components: list[Component]


class DraftResponse(BaseModel):
    workspace: str
    release: int | None = Field(description="The current release the draft is compared with.")
    valid: bool
    issues: list[IssueOut]
    files: list[FileChange] = Field(description="Files changed since the current release.")
    items: list[DraftItem] = Field(description="The same changes as agents, shared instructions, procedures, groups and settings.")
    agents: list[DraftAgent]
    lint: list[LintFinding] = Field([], description="`sopc lint` on the draft: duplicated or conflicting instructions. Advisory; publishing isn't blocked.")


class ReleaseRequest(BaseModel):
    author: str | None = None
    note: str | None = None


class ReleaseSummary(BaseModel):
    number: int
    created_at: datetime
    author: str | None
    note: str | None
    files: dict[str, int] = Field(description="{path: version} snapshot.")
    current: bool


class ReleaseResponse(BaseModel):
    release: ReleaseSummary
    created: bool = Field(description="False if head was already the current release.")


class PublishRequest(ReplaceRequest):
    pass


class PublishResponse(ReleaseResponse):
    changed: list[WrittenFile]


class ReleasedAgent(BaseModel):
    platform_ref: str
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str]
    components: list[Component]


class ReleaseDetail(ReleaseSummary):
    agents: dict[str, ReleasedAgent]


class AgentSummary(BaseModel):
    agent: str
    platform_ref: str
    hash: str


class AgentsResponse(BaseModel):
    workspace: str
    release: int
    agents: list[AgentSummary]


class AgentOut(BaseModel):
    agent: str = Field(description="sopc agent id.")
    platform_ref: str
    release: int
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str] = Field(description="Ids of SOPs to fetch with `get_sop` (`/sops/{sop_id}`).")
    components: list[Component] = Field(description="Each file the prompt was built from, at its version. Put this and `release` in call metadata.")


class FetchOut(BaseModel):
    id: int
    agent: str
    release: int
    hash: str
    at: datetime
