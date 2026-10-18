"""Serving: what each agent loads at call start (prompt fetches take a runtime-scoped token once the workspace has one), and the fetch log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..errors import NotFound
from ..runtime.service import Runtime
from ..schemas import AgentOut, AgentsResponse, FetchOut
from ..store import Store
from .deps import OPEN


def router(store: Store, runtime: Runtime) -> APIRouter:
    r = APIRouter()

    runtime_bearer = HTTPBearer(auto_error=False, description="A token with the runtime scope (Settings → Tokens), once the workspace has one.")

    def runtime_token(workspace: str, credentials: HTTPAuthorizationCredentials | None = Depends(runtime_bearer)) -> None:
        runtime.check_fetch(workspace, f"Bearer {credentials.credentials}" if credentials else None)

    fetch_auth = [Depends(runtime_token)]

    @r.get("/v1/workspaces/{workspace}/agents", response_model=AgentsResponse, dependencies=OPEN, tags=["serving"])
    def list_agents(workspace: str, release: int | None = None) -> dict:
        rel = store.get_release(workspace, release)
        agents = [{"agent": a, "platform_ref": b["platform_ref"], "hash": b["hash"]} for a, b in sorted(rel["build"]["agents"].items())]
        return {"workspace": workspace, "release": rel["number"], "agents": agents}

    @r.get("/v1/workspaces/{workspace}/agents/{agent}", response_model=AgentOut, dependencies=fetch_auth, tags=["serving"])
    def get_agent(workspace: str, agent: str, release: int | None = Query(None, description="Pin a release (e.g. for A/B); default: current.")) -> dict:
        """What to load at call start. `agent` is the sopc id or platform ref (`livekit:tonys-pizza`). Logs a fetch."""
        served = store.agent(workspace, agent, release)
        store.log_fetch(workspace, served["agent"], served["release"], served["hash"])
        return {"agent": served["agent"], "release": served["release"], **released(served)}

    @r.get(
        "/v1/workspaces/{workspace}/agents/{agent}/prompt",
        response_class=PlainTextResponse,
        dependencies=fetch_auth,
        tags=["serving"],
        responses={200: {"content": {"text/plain": {}}, "description": "The full prompt, with X-Sopc-Release and X-Sopc-Hash headers."}},
    )
    def get_prompt(workspace: str, agent: str, response: Response, release: int | None = None) -> str:
        """Just the prompt text. Logs a fetch."""
        served = store.agent(workspace, agent, release)
        store.log_fetch(workspace, served["agent"], served["release"], served["hash"])
        response.headers["X-Sopc-Release"] = str(served["release"])
        response.headers["X-Sopc-Hash"] = served["hash"]
        return served["prompt"]

    @r.get("/v1/workspaces/{workspace}/agents/{agent}/sops/{sop_id}", response_model=dict[str, Any], dependencies=fetch_auth, tags=["serving"])
    def get_sop(workspace: str, agent: str, sop_id: str, release: int | None = None) -> dict:
        """What the `get_sop` tool returns for SOPs with `delivery: auto | tool`."""
        served = store.agent(workspace, agent, release)
        if sop_id not in served["sops"]:
            raise NotFound(f"SOP '{sop_id}' is not tool-delivered to '{served['agent']}'")
        return served["sops"][sop_id]

    @r.get("/v1/workspaces/{workspace}/fetches", response_model=list[FetchOut], dependencies=OPEN, tags=["serving"])
    def list_fetches(workspace: str, agent: str | None = Query(None, description="sopc agent id."), limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        """Which release and prompt hash each agent was served, newest first."""
        return store.fetches(workspace, agent, limit)

    return r


def released(b: dict) -> dict:
    """An agent's entry in a release build, as the API shows it."""
    return {
        "platform_ref": b["platform_ref"],
        "hash": b["hash"],
        "prompt": b["prompt"],
        "tools": b["tools"],
        "tool_sops": sorted(b["sops"]),
        "components": b["components"],
    }
