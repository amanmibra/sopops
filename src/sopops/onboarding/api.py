"""Onboarding endpoints: imports under `/v1/workspaces/<ws>/imports`, and the token-authenticated endpoints a coding
agent uses through `sopops-mcp` under `/v1/onboarding`."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..llm import LLMError, model_for
from ..schemas import WrittenFile
from ..store import Store
from ..tokens import Forbidden, Unauthorized
from . import platforms
from .models import Finding, Proposal, ProposalContent, SourcePrompt
from .service import DONE, Onboarding

# --- request and response shapes ------------------------------------------------------------


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
    claude_code: str = Field(description="Adds the sopops MCP server to Claude Code.")
    codex: str = Field(description="Adds it to Codex.")
    codex_toml: str = Field(description="The same, as ~/.codex/config.toml lines.")
    prompt: str = Field(description="What to paste into the coding agent.")
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


# --- routes --------------------------------------------------------------------------------


def router(store: Store, onboarding: Onboarding) -> APIRouter:
    r = APIRouter(tags=["onboarding"])
    base = "/v1/workspaces/{workspace}/imports"

    def agent(authorization: str | None):
        try:
            return onboarding.authenticate(authorization)
        except Unauthorized as e:
            raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from None
        except Forbidden as e:
            raise HTTPException(403, str(e)) from None

    @r.get("/v1/onboarding/capabilities", response_model=Capabilities)
    def onboarding_capabilities() -> dict:
        """What this server can do for onboarding: extract with its own model, and which platforms it reads."""
        return {"llm": onboarding.llm is not None, "model": model_for("reasoning"), "platforms": list(platforms.PLATFORMS)}

    @r.post("/v1/onboarding/platform-agents", response_model=list[PlatformAgent], responses={400: {"description": "The platform refused the key or couldn't be reached."}})
    def list_platform_agents(req: PlatformAgentsRequest) -> list[dict]:
        """The agents an ElevenLabs or Vapi key can see. The key is used for this request only, never stored or logged."""
        try:
            return platforms.list_agents(req.platform, req.api_key)
        except platforms.PlatformError as e:
            raise HTTPException(400, str(e)) from None

    @r.get(base, response_model=list[ImportOut])
    def list_imports(workspace: str) -> list[dict]:
        return onboarding.list(workspace)

    @r.post(base, response_model=ImportOut, status_code=201, responses={400: {"description": "No prompts, or a platform error."}, 503: {"description": "No model configured."}})
    def create_import(workspace: str, req: CreateImport) -> dict:
        """Extract blocks and agents from existing prompts with sopops's model, in the background. Poll the import (or
        stream `…/stream`) until it's `ready`, then review its proposal. A platform key is used to read the agents during
        this request and is never stored."""
        sources = list(req.sources)
        if req.platform:
            try:
                for agent_id in req.platform.agent_ids:
                    got = platforms.get_agent(req.platform.platform, req.platform.api_key, agent_id)
                    if not got["prompt"].strip():
                        raise HTTPException(400, f"{got['name']} has no prompt to import")
                    sources.append(SourcePrompt(name=got["name"], platform=got["platform"], platform_id=got["platform_id"], prompt=got["prompt"], tools=got["tools"]))
            except platforms.PlatformError as e:
                raise HTTPException(400, str(e)) from None
        try:
            return onboarding.start(workspace, sources, req.author)
        except LLMError as e:
            raise HTTPException(503, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @r.post(f"{base}/agent", response_model=AgentImportOut, status_code=201, response_model_by_alias=True)
    def start_agent_import(workspace: str, req: AgentImportRequest, request: Request) -> dict:
        """An onboarding token for Claude Code or Codex (scope onboarding:write, this workspace only, 2 hours), and the
        commands and prompt to start one. The token is shown only in this response."""
        server = (req.server_url or str(request.base_url)).rstrip("/")
        return onboarding.start_agent(workspace, server, req.mode, req.repo, req.author)

    @r.get(f"{base}/{{id}}", response_model=ImportOut)
    def get_import(workspace: str, id: str) -> dict:
        return onboarding.get(workspace, id)

    @r.get(f"{base}/{{id}}/events", response_model=list[EventOut])
    def import_events(workspace: str, id: str, after: int = 0) -> list[dict]:
        """Progress messages after event `after`, oldest first."""
        return onboarding.events(workspace, id, after)

    @r.get(f"{base}/{{id}}/stream", responses={200: {"content": {"text/event-stream": {}}, "description": "`progress` events, then `status` whenever it changes."}})
    def import_stream(workspace: str, id: str, after: int = 0) -> StreamingResponse:
        """Server-sent events: each progress message as it arrives, and the import's status when it changes.
        Ends when the import is applied or discarded, or after 10 minutes (reconnect with `after`)."""
        onboarding.get(workspace, id)

        def gen():
            last, status, deadline = after, None, time.monotonic() + 600
            while time.monotonic() < deadline:
                for e in onboarding.events(workspace, id, last):
                    last = e["id"]
                    yield f"id: {last}\nevent: progress\ndata: {json.dumps(e, default=str)}\n\n"
                cur = onboarding.get(workspace, id)
                if cur["status"] != status:
                    status = cur["status"]
                    yield f"event: status\ndata: {json.dumps({'status': status, 'pushes': cur['pushes']})}\n\n"
                if status in DONE:
                    return
                time.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @r.put(f"{base}/{{id}}/proposal", response_model=ImportOut)
    def update_import_proposal(workspace: str, id: str, req: ProposalContent) -> dict:
        """Save edits to the proposal; fidelity and sopc's checks are run again."""
        return onboarding.update(workspace, id, req)

    @r.post(f"{base}/{{id}}/findings/{{finding}}", response_model=ImportOut, responses={400: {"description": "Not a choice for this finding."}})
    def resolve_import_finding(workspace: str, id: str, finding: str, req: ResolveRequest) -> dict:
        """Decide drift: keep each agent's version, or give every agent one agent's version."""
        try:
            return onboarding.resolve(workspace, id, finding, req.choice)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @r.post(f"{base}/{{id}}/apply", response_model=ApplyOut, response_model_by_alias=True, responses={409: {"description": "Already applied, or an id clashes."}})
    def apply_import(workspace: str, id: str, req: ApplyRequest | None = None) -> dict:
        """Write the proposal's blocks, groups and agents as drafts (nothing is published). Ends the onboarding token."""
        req = req or ApplyRequest()
        return onboarding.apply(workspace, id, req.author, req.note, req.proposal)

    @r.post(f"{base}/{{id}}/revoke", response_model=ImportOut)
    def revoke_import_token(workspace: str, id: str) -> dict:
        """End the import's onboarding token now (Finish). The proposal stays for review."""
        return onboarding.revoke(workspace, id)

    @r.delete(f"{base}/{{id}}", response_model=ImportOut)
    def discard_import(workspace: str, id: str) -> dict:
        """Discard the import and end its token. Nothing in the workspace changes."""
        return onboarding.discard(workspace, id)

    # the coding agent, with its onboarding token

    @r.get("/v1/onboarding/session", response_model=SessionInfo)
    def onboarding_session(authorization: str | None = Header(None)) -> dict:
        """The workspace and mode an onboarding token is for."""
        rec = agent(authorization)
        token = onboarding.token_of(rec) or {}
        return {"workspace": rec.workspace, "import_id": rec.id, "mode": rec.mode, "repo": rec.repo, "scopes": token.get("scopes", []), "expires_at": token.get("expires_at")}

    @r.post("/v1/onboarding/progress", response_model=EventOut)
    def onboarding_progress(req: ProgressIn, authorization: str | None = Header(None)) -> dict:
        """A progress message for the person watching the import in sopops."""
        return onboarding.progress(agent(authorization), req.step, req.message)

    @r.put("/v1/onboarding/proposal", response_model=PushOut)
    def onboarding_push(req: PushIn, authorization: str | None = Header(None)) -> dict:
        """Push the proposal for review; it replaces any earlier push. Returns fidelity per agent."""
        return onboarding.push(agent(authorization), req.proposal, req.originals, req.findings, req.notes)

    @r.post("/v1/onboarding/fidelity", response_model=FidelityOut)
    def onboarding_check(req: FidelityIn, authorization: str | None = Header(None)) -> dict:
        """Compile a proposal with sopc and compare each agent with its original. Stores nothing."""
        agent(authorization)
        return onboarding.check(req.proposal, req.originals)

    return r
