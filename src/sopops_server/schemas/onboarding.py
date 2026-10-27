"""Onboarding: imports, the coding agent's session, proposals and fidelity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..onboarding.types import (
    AgentFidelity,
    Finding,
    Proposal,
    ProposalContent,
    SourcePrompt,
)
from .files import WrittenFile


class PlatformImport(BaseModel):
    platform: Literal["elevenlabs", "vapi"]
    api_key: str = Field(description="Used for this request only: never stored or logged.")
    agent_ids: list[str] = Field(min_length=1)


class CreateImport(BaseModel):
    sources: list[SourcePrompt] = Field(default_factory=list, description="Pasted or uploaded instructions, one per agent.")
    platform: PlatformImport | None = Field(None, description="Agents to read from ElevenLabs or Vapi, with a key used only for this request.")
    guidance: str | None = Field(None, max_length=2000, description="How the user wants their instructions organized (e.g. \"keep each restaurant's menu as its own block\"), "
                                 "passed to the model. Empty or missing: sopops's default choices.")
    author: str | None = None


class ProjectImport(BaseModel):
    files: dict[str, str] | None = Field(None, description="The sopc project's files, by path relative to its folder (the one with sopc.yaml). build/ is ignored.")
    from_repo: bool = Field(False, description="Read the project from the workspace's GitHub repository (its branch's head, its folder) instead of `files`.")
    originals: dict[str, str] = Field(default_factory=dict, description="Each agent's original instructions, by agent id, to check the project against (optional).")
    author: str | None = None


class OriginalsIn(BaseModel):
    originals: dict[str, str] = Field(description="Original instructions by agent id; an empty one removes it.")


class PlatformAgentsRequest(BaseModel):
    platform: Literal["elevenlabs", "vapi"]
    api_key: str = Field(description="Used for this request only: never stored or logged.")


class PlatformAgent(BaseModel):
    id: str
    name: str


class AgentImportRequest(BaseModel):
    mode: Literal["hosted", "repo"] = Field("hosted", description="hosted: instructions live in sopops. repo: they live in the workspace's connected "
                                            "GitHub repository too; unless its folder already holds a project sopc builds, the agent opens a pull request adding it.")
    server_url: str | None = Field(None, description="The URL the coding agent reaches sopops at. Default: this request's base URL.")
    author: str | None = None


class ImportTokenOut(BaseModel):
    prefix: str | None
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool


class AgentClient(BaseModel):
    name: str | None = Field(description="The MCP client's clientInfo name, e.g. claude-code; null when it didn't say.")
    version: str | None
    label: str | None = Field(description="The product, when sopops knows the client: Claude Code or Codex.")
    connected_at: datetime = Field(description="When the coding agent first reached sopops with the onboarding token.")


class ImportOut(BaseModel):
    id: str
    workspace: str
    source: Literal["llm", "agent", "sopc"] = Field(description="llm: sopops's model extracted it; agent: a coding agent pushed it; sopc: an existing sopc project.")
    status: Literal["waiting", "running", "ready", "failed", "applied", "discarded"]
    mode: str
    repo: str | None = Field(description="Repo mode: the connected repository (owner/name).")
    repo_branch: str | None
    repo_path: str | None = Field(description="Repo mode: the sopc project's folder; empty for the root.")
    repo_project: Literal["missing", "invalid", "valid"] | None = Field(description="Repo mode: what the folder held when the import started.")
    pr_url: str | None = Field(description="The onboarding pull request the coding agent opened.")
    pr_state: Literal["open", "merged", "closed"] | None = Field(description="Followed on GitHub. Merged: the folder was pulled into drafts.")
    pr_merged_at: datetime | None
    agents: list[str]
    guidance: str | None = Field(None, description="llm: how the user asked for their instructions to be organized.")
    error: str | None
    pushes: int
    token: ImportTokenOut | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    agent_client: AgentClient | None = Field(None, description="The coding agent, once it has connected (agent imports).")
    proposal: Proposal | None = None


class Kickoff(BaseModel):
    claude_code: str = Field(description="One command: adds the sopops MCP server to Claude Code and starts it on `prompt`.")
    codex: str = Field(description="The same for Codex.")
    codex_toml: str = Field(description="The MCP server as ~/.codex/config.toml lines.")
    prompt: str = Field(description="The instructions the commands start the coding agent with: call get_kickoff and follow it.")
    settings_claude_code: str = Field(description="Recommended Claude Code settings, as editable text.")
    settings_codex: str = Field(description="Recommended Codex settings, as editable text.")
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
    client: str | None = Field(None, description="For the coding agent's messages: which agent (Claude Code, Codex), when its MCP client said.")


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
    repo_branch: str | None
    repo_path: str | None
    repo_project: Literal["missing", "invalid", "valid"] | None
    repo_auth: Literal["token", "app"] | None = Field(None, description="How sopops signs in to the repository: `app` means sopops opens the onboarding pull request.")
    pr_url: str | None
    scopes: list[str]
    expires_at: datetime | None


class PullRequestIn(BaseModel):
    url: str = Field(max_length=1024, description="The onboarding pull request, e.g. https://github.com/acme/agents/pull/12.")


class OpenPullRequestIn(BaseModel):
    description: str | None = Field(None, max_length=20000, description="The pull request's description: the agents, where each one's instructions were found, the blocks, drift found.")


class ProgressIn(BaseModel):
    step: str = Field(max_length=64)
    message: str = Field(max_length=4000)


class PushIn(BaseModel):
    proposal: ProposalContent
    originals: dict[str, str] = Field(default_factory=dict, description="Each agent's original instructions, by agent id.")
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


class KickoffOut(BaseModel):
    instructions: str = Field(description="The sopops-onboard skill, then this session: workspace, server, mode, and in repo mode what to do there.")


class GuideOut(BaseModel):
    guide: str = Field(description="How to turn agents' instructions into a proposal.")
    proposal_schema: dict[str, Any]
    finding_schema: dict[str, Any]


class SkillOut(BaseModel):
    name: str
    text: str = Field(description="SKILL.md, with its frontmatter.")


class ExtractionIn(BaseModel):
    prompts: list[SourcePrompt] = Field(min_length=1)


class ExtractionUnit(BaseModel):
    n: int
    text: str


class ExtractionInput(BaseModel):
    id: str
    name: str
    platform: str
    platform_id: str
    tools: list[str]
    units: list[ExtractionUnit]


class ExtractionOut(BaseModel):
    mode: Literal["host"]
    instructions: str = Field(description="The guide, then how to classify units and structure procedures.")
    inputs: list[ExtractionInput]
    originals: dict[str, str] = Field(description="Each agent's original instructions, by agent id.")
    proposal_schema: dict[str, Any]
    next: str


class ProjectIn(BaseModel):
    proposal: ProposalContent


class ProjectOut(BaseModel):
    files: dict[str, str] = Field(description="The sopc project's files by path: sopc.yaml, instructions/, procedures/, agents/.")
    issues: list[dict[str, Any]] = Field(description="Problems that kept items from being written.")
    validation: list[dict[str, Any]] = Field(description="`sopc validate` on the files.")


# --- the review, as summaries ----------------------------------------------------------------------------------------


class ReviewCounts(BaseModel):
    agents: int
    shared_blocks: int = Field(description="Proposed blocks more than one agent uses.")
    variables: int
    disagreements: int
    undecided: int = Field(description="Disagreements with no decision yet.")
    agents_with_unique: int = Field(description="Agents with text no other agent has.")
    below_full_fidelity: int = Field(description="Agents whose full instructions lost or changed a sentence of the original.")


class WordingGroup(BaseModel):
    text: str
    agents: list[str]


class Disagreement(BaseModel):
    id: str = Field(description="sopc overlap's group id (stable across runs), or the extraction's finding id.")
    source: Literal["overlap", "model"] = Field(description="overlap: a rule word differs (sopc); model: suggested by the model (a paraphrased conflict).")
    majority: WordingGroup
    outliers: list[WordingGroup]
    minority: int = Field(description="How many agents aren't in the majority.")
    words: dict[str, str] = Field(description="Each agent's differing words.")
    decision: str | None = Field(description="keep, or all:<agent>.")


class VariableRow(BaseModel):
    name: str
    values: dict[str, str | None] = Field(description="Each agent's value.")
    source: Literal["proposal", "overlap"] = Field(description="proposal: named by the model; overlap: found by sopc and not in the proposal (its name is a default).")
    id: str | None


class BlockRow(BaseModel):
    kind: Literal["instruction", "procedure"]
    id: str
    title: str
    agents: list[str] = Field(description="The agents that use it.")
    missing: list[str] = Field(description="Agents that don't use it, though most agents' instructions have its text.")


class UniqueText(BaseModel):
    id: str
    agent: str
    text: str
    decision: Literal["context", "block"]


class ReviewOut(BaseModel):
    counts: ReviewCounts
    disagreements: list[Disagreement]
    variables: list[VariableRow]
    blocks: list[BlockRow]
    unique: list[UniqueText]
    fidelity: list[AgentFidelity]


class DecisionIn(BaseModel):
    choice: str = Field(description="A disagreement: keep, or all:<agent>. Unique text: context or block.")


class VariableEdit(BaseModel):
    name: str | None = Field(None, description="A new name (snake_case).")
    values: dict[str, str] = Field(default_factory=dict, description="New values, by agent.")
