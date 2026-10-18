"""Onboarding: imports, the coding agent's session, proposals and fidelity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..onboarding.types import Finding, Proposal, ProposalContent, SourcePrompt
from .files import WrittenFile


class PlatformImport(BaseModel):
    platform: Literal["elevenlabs", "vapi"]
    api_key: str = Field(description="Used for this request only: never stored or logged.")
    agent_ids: list[str] = Field(min_length=1)


class CreateImport(BaseModel):
    sources: list[SourcePrompt] = Field(default_factory=list, description="Pasted or uploaded prompts, one per agent.")
    platform: PlatformImport | None = Field(None, description="Agents to read from ElevenLabs or Vapi, with a key used only for this request.")
    author: str | None = None


class PlatformAgentsRequest(BaseModel):
    platform: Literal["elevenlabs", "vapi"]
    api_key: str = Field(description="Used for this request only: never stored or logged.")


class PlatformAgent(BaseModel):
    id: str
    name: str


class AgentImportRequest(BaseModel):
    mode: Literal["hosted", "repo"] = Field("hosted", description="hosted: instructions live in sopops. repo: the agent also opens a PR adding sops/.")
    repo: str | None = Field(None, description="Repo mode: the repository URL or path (recorded; syncing from it comes later).")
    server_url: str | None = Field(None, description="The URL the coding agent reaches sopops at. Default: this request's base URL.")
    author: str | None = None


class ImportTokenOut(BaseModel):
    prefix: str | None
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool


class ImportOut(BaseModel):
    id: str
    workspace: str
    source: Literal["llm", "agent"]
    status: Literal["waiting", "running", "ready", "failed", "applied", "discarded"]
    mode: str
    repo: str | None
    agents: list[str]
    error: str | None
    pushes: int
    token: ImportTokenOut | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    proposal: Proposal | None = None


class Kickoff(BaseModel):
    claude_code: str = Field(description="One command: adds the sopops MCP server to Claude Code and starts it on `prompt`.")
    codex: str = Field(description="The same for Codex.")
    codex_toml: str = Field(description="The MCP server as ~/.codex/config.toml lines.")
    prompt: str = Field(description="The prompt the commands start the agent with: call get_kickoff and follow it.")
    settings: str = Field(description="Recommended settings, as editable text.")
    mcp_source: str


class AgentImportOut(BaseModel):
    import_: ImportOut = Field(alias="import")
    token: str = Field(description="Shown once. Scoped to onboarding:write for this workspace; expires in 2 hours.")
    kickoff: Kickoff

    model_config = {"populate_by_name": True}


class EventOut(BaseModel):
    id: int
    at: datetime
    step: str
    message: str
    by: Literal["sopops", "agent"]


class ResolveRequest(BaseModel):
    choice: str = Field(description="'keep' (each agent keeps its version), or the agent whose version every agent gets.")


class ApplyRequest(BaseModel):
    proposal: ProposalContent | None = Field(None, description="The edited proposal, if it changed since the last save.")
    author: str | None = None
    note: str | None = None


class ApplyOut(BaseModel):
    import_: ImportOut = Field(alias="import")
    changed: list[WrittenFile]
    replaced: list[str] = Field(description="Items that already existed and were replaced, as kind:id.")

    model_config = {"populate_by_name": True}


class Capabilities(BaseModel):
    llm: bool = Field(description="Whether sopops can extract with its own model (SOPOPS_MODEL_REASONING is set).")
    model: str | None
    platforms: list[str]


class SessionInfo(BaseModel):
    workspace: str
    import_id: str
    mode: str
    repo: str | None
    scopes: list[str]
    expires_at: datetime | None


class ProgressIn(BaseModel):
    step: str = Field(max_length=64)
    message: str = Field(max_length=4000)


class PushIn(BaseModel):
    proposal: ProposalContent
    originals: dict[str, str] = Field(default_factory=dict, description="Each agent's original prompt, by agent id.")
    findings: list[Finding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FidelityIn(BaseModel):
    proposal: ProposalContent
    originals: dict[str, str]


class FidelityOut(BaseModel):
    fidelity: list[dict[str, Any]]
    issues: list[dict[str, Any]]


class PushOut(FidelityOut):
    id: str
    pushes: int
