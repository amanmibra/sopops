"""Serving: what each agent loads at call start (instructions fetches take a runtime-scoped token once the workspace has one, or\nalways with sign-in on, unless someone signed in is looking), and the fetch log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import access
from ..errors import NotFound
from ..runtime.service import Runtime
from ..schemas import AgentOut, AgentsResponse, FetchOut
from ..store import Store
from .deps import OPEN


def router(store: Store, runtime: Runtime) -> APIRouter:
    r = APIRouter()

    runtime_bearer = HTTPBearer(auto_error=False, description="A token with the runtime scope (Settings → Tokens), once the workspace has one.")

    def runtime_token(workspace: str, request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(runtime_bearer)) -> None:
        if access.signed_in(request) is None:  # someone signed in may look; agents send their runtime token
            runtime.check_fetch(workspace, f"Bearer {credentials.credentials}" if credentials else None)

    fetch_auth = [Depends(runtime_token)]

    @r.get("/v1/workspaces/{workspace}/agents", response_model=AgentsResponse, dependencies=OPEN, tags=["serving"])
    def list_agents(workspace: str, release: int | None = None) -> dict:
        rel = store.get_release(workspace, release)
        agents = [{"agent": a, "platform_ref": b["platform_ref"], "hash": b["hash"]} for a, b in sorted(rel["build"]["agents"].items())]
        return {"workspace": workspace, "release": rel["number"], "agents": agents}

    @r.get("/v1/workspaces/{workspace}/agents/{agent}", response_model=AgentOut, dependencies=fetch_auth, tags=["serving"])
    def get_agent(workspace: str, agent: str, release: int | None = Query(None, description="Pin a release (e.g. for A/B); default: current."),
                  platform: str | None = Query(None, description="Scope a raw platform id to this platform, e.g. `livekit`.")) -> dict:
        """What to load at call start. `agent` is the sop id, platform ref, or a platform id when `platform` scopes it. Logs a fetch."""
        served = store.agent(workspace, agent, release, platform)
        store.log_fetch(workspace, served["agent"], served["release"], served["hash"])
        return {"agent": served["agent"], "release": served["release"], **released(served)}

    @r.get(
        "/v1/workspaces/{workspace}/agents/{agent}/prompt",
        response_class=PlainTextResponse,
        dependencies=fetch_auth,
        tags=["serving"],
        responses={200: {"content": {"text/plain": {}}, "description": "The agent's full instructions, with X-Sop-Release and X-Sop-Hash headers."}},
    )
    def get_prompt(workspace: str, agent: str, response: Response, release: int | None = None, platform: str | None = None) -> str:
        """Just the instructions text. Logs a fetch."""
        served = store.agent(workspace, agent, release, platform)
        store.log_fetch(workspace, served["agent"], served["release"], served["hash"])
        response.headers["X-Sop-Release"] = str(served["release"])
        response.headers["X-Sop-Hash"] = served["hash"]
        return served["prompt"]

    @r.get("/v1/workspaces/{workspace}/agents/{agent}/sops/{sop_id}", response_model=dict[str, Any], dependencies=fetch_auth, tags=["serving"])
    def get_sop(workspace: str, agent: str, sop_id: str, release: int | None = None, platform: str | None = None) -> dict:
        """What the `get_sop` tool returns for SOPs with `delivery: auto | tool`."""
        served = store.agent(workspace, agent, release, platform)
        if sop_id not in served["sops"]:
            raise NotFound(f"SOP '{sop_id}' is not tool-delivered to '{served['agent']}'")
        return served["sops"][sop_id]

    @r.get("/v1/workspaces/{workspace}/fetches", response_model=list[FetchOut], dependencies=OPEN, tags=["serving"])
    def list_fetches(workspace: str, agent: str | None = Query(None, description="sop agent id."), limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        """Which release and instructions hash each agent was served, newest first."""
        return store.fetches(workspace, agent, limit)

    return r


def released(b: dict) -> dict:
    """An agent's entry in a release build, as the API shows it."""
    return {
        "platform_ref": b["platform_ref"],
        "hash": b["hash"],
        "prompt": b["prompt"],
        "template": b.get("template", b["prompt"]),  # releases built before sop v0.1.2 have neither
        "variables": b.get("variables", {}),
        "tools": b["tools"],
        "tool_sops": sorted(b["sops"]),
        "components": b["components"],
    }
