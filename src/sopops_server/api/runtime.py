"""Runtime delivery: its routes, CORS for browsers and its error handlers, added to the app by `register`.

Under /v1/workspaces/<ws>: `runtime/delivery` (each agent's mode, its platform's), `runtime/platforms` (API keys, webhook
secrets, write-only, and each platform's delivery mode), `releases/<n>/pushes` (push results, retry), and the platforms' call-start webhooks
`runtime/elevenlabs/conversation-init` and `runtime/vapi/assistant-request`. The Vapi Server URL also takes the
messages that bring sessions (`capture.vapi_message`), and the ElevenLabs one starts live monitoring when it's on.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Query,
    Request,
    Response,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..capture import Capture, LiveConversationRef
from ..errors import Conflict
from ..runtime.crypto import SecretKeyMissing
from ..runtime.service import AssistantCache, BadRequest, Runtime
from ..schemas.runtime import (
    AgentDeliveryOut,
    DeliveryIn,
    KeyTestIn,
    KeyTestOut,
    PlatformCredentials,
    PushPlatform,
    PushResultOut,
    RetryIn,
    SecretIn,
    SecretKind,
    SecretSaved,
    ServedIn,
    ServedOut,
)
from ..store import Store
from .capture import vapi_message

WS = "/v1/workspaces/{workspace}"


def register(app: FastAPI, store: Store) -> Runtime:
    runtime = Runtime(store)
    capture = Capture(store)
    assistants = AssistantCache()
    app.state.runtime = runtime
    r = APIRouter()
    bearer = HTTPBearer(auto_error=False, description="A token with the runtime scope (Settings → Tokens), once the workspace has one.")

    # Browsers calling sopops directly (the TypeScript client's ElevenLabs adapter in a web app) need CORS.
    if origins := [o.strip() for o in os.environ.get("SOPOPS_CORS_ORIGINS", "").split(",") if o.strip()]:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(BadRequest)
    def _bad_request(_, exc: BadRequest) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(SecretKeyMissing)
    def _no_secret_key(_, exc: SecretKeyMissing) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # delivery mode

    @r.get(f"{WS}/runtime/delivery", response_model=list[AgentDeliveryOut], tags=["runtime"])
    def list_delivery(workspace: str) -> list[dict]:
        """Every agent at head, with how it gets its instructions (its platform's mode): `pull`, `push` or `both`."""
        return runtime.deliveries(workspace)

    # before `runtime/platforms/{platform}/{kind}`, which would take `delivery` for a kind and refuse it
    @r.put(f"{WS}/runtime/platforms/{{platform}}/delivery", response_model=DeliveryIn, tags=["runtime"])
    def set_platform_delivery(workspace: str, platform: PushPlatform, req: DeliveryIn) -> dict:
        """Set how every agent on a platform gets its instructions. Push needs the platform's API key; LiveKit agents only pull."""
        return {"mode": runtime.set_platform_delivery(workspace, platform, req.mode)}

    # platform credentials: write-only

    @r.get(f"{WS}/runtime/platforms", response_model=list[PlatformCredentials], tags=["runtime"])
    def list_platforms(workspace: str) -> list[dict]:
        """Which platform API keys and webhook secrets are stored. Never the values."""
        return runtime.credentials(workspace)

    @r.put(f"{WS}/runtime/platforms/{{platform}}/{{kind}}", response_model=SecretSaved, tags=["runtime"])
    def set_platform_secret(workspace: str, platform: PushPlatform, kind: SecretKind, req: SecretIn) -> dict:
        """Store an API key or webhook secret, encrypted with SOPOPS_SECRET_KEY. A webhook secret sent empty is
        generated and returned this once. 503 without SOPOPS_SECRET_KEY."""
        return runtime.set_secret(workspace, platform, kind.replace("-", "_"), req.value)

    @r.delete(f"{WS}/runtime/platforms/{{platform}}/{{kind}}", status_code=204, tags=["runtime"])
    def delete_platform_secret(workspace: str, platform: PushPlatform, kind: SecretKind) -> Response:
        runtime.delete_secret(workspace, platform, kind.replace("-", "_"))
        return Response(status_code=204)

    @r.post(f"{WS}/runtime/platforms/{{platform}}/test", response_model=KeyTestOut, tags=["runtime"])
    def test_platform_key(workspace: str, platform: PushPlatform, req: KeyTestIn | None = None) -> dict:
        """Read one agent with the stored API key (a GET; nothing changes)."""
        return runtime.test_key(workspace, platform, (req or KeyTestIn()).platform_id)

    # push results

    @r.get(f"{WS}/releases/{{number}}/pushes", response_model=list[PushResultOut], tags=["runtime"])
    def list_pushes(workspace: str, number: int) -> list[dict]:
        """Each push-mode agent's latest push of this release: `ok`, `unchanged` (already live there) or `error`."""
        store.get_release(workspace, number)  # 404 if it doesn't exist
        return runtime.push_results(workspace, number)

    @r.post(f"{WS}/releases/{{number}}/pushes/retry", response_model=list[PushResultOut], tags=["runtime"])
    def retry_pushes(workspace: str, number: int, req: RetryIn | None = None) -> list[dict]:
        """Push again: the given agents, or every agent whose last push failed. Only the live release is pushed (409 otherwise)."""
        rel = store.get_release(workspace, number)
        if not rel["current"]:
            raise Conflict(f"release {number} isn't live; make it live to push it")
        req = req or RetryIn()
        return runtime.push_release(workspace, number, agents=req.agents, only_failed=req.agents is None)

    # a client fetched the prompt itself (e.g. ElevenLabs overrides from a web app) and reports the call it ran

    @r.post(f"{WS}/runtime/served", response_model=ServedOut, tags=["runtime"], responses={401: {"description": "Missing or wrong runtime token."}})
    def record_served(workspace: str, req: ServedIn, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        """Remember which release a call ran on, once its id is known, so the session that arrives later (an
        ElevenLabs post-call webhook) links to it. Takes a runtime-scoped token, like instructions fetches."""
        runtime.check_fetch(workspace, f"Bearer {credentials.credentials}" if credentials else None)
        served = runtime.resolve(workspace, req.platform, req.agent, req.release)
        runtime.record_serve(workspace, req.platform, [req.call_id], served, log_fetch=False)
        return {"platform": req.platform, "call_id": req.call_id, "agent": served["agent"], "release": served["release"]}

    # call-start webhooks: the platform asks for the prompt (pull)

    async def verified(request: Request, workspace: str, platform: str) -> dict[str, Any]:
        raw = await request.body()
        await run_in_threadpool(runtime.check_webhook, workspace, platform, request.headers, raw)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise BadRequest("the body isn't JSON") from None
        if not isinstance(body, dict):
            raise BadRequest("the body isn't a JSON object")
        return body

    @r.post(
        f"{WS}/runtime/elevenlabs/conversation-init",
        tags=["runtime"],
        response_model=dict[str, Any],
        responses={401: {"description": "Not signed with the workspace's webhook secret or runtime token."}},
    )
    async def elevenlabs_conversation_init(
        workspace: str,
        request: Request,
        agent: str | None = Query(None, description="The agent; default: from `agent_id` in the body."),
        release: int | None = Query(None, description="Pin a release; default: the live one."),
    ) -> dict:
        """ElevenLabs' conversation-initiation webhook: returns the agent's full instructions as
        `conversation_config_override.agent.prompt.prompt`, and the release as the dynamic variable `sopops_release`.
        The agent must allow overriding its system prompt (ElevenLabs' name for the instructions) under Security → Overrides. With Enterprise live monitoring on
        (Settings → Platforms), the conversation is then followed on ElevenLabs' monitor socket."""
        body = await verified(request, workspace, "elevenlabs")

        def serve() -> tuple[dict, bool]:
            served = runtime.resolve(workspace, "elevenlabs", agent or body.get("agent_id"), release)
            runtime.record_serve(workspace, "elevenlabs", [body.get("conversation_id"), body.get("call_sid")], served)
            setting = capture.setting(workspace, "elevenlabs")
            return served, bool(setting and setting.live)

        served, live = await run_in_threadpool(serve)
        if live and isinstance(body.get("conversation_id"), str) and body["conversation_id"]:
            request.app.state.jobs.watch_elevenlabs(LiveConversationRef(
                workspace_id=workspace, conversation_id=body["conversation_id"], agent=served["agent"], release=served["release"]))
        return {
            "type": "conversation_initiation_client_data",
            "dynamic_variables": {"sopops_release": served["release"], "sopops_agent": served["agent"]},
            "conversation_config_override": {"agent": {"prompt": {"prompt": served["prompt"]}}},
        }

    @r.post(
        f"{WS}/runtime/vapi/assistant-request",
        tags=["runtime"],
        response_model=dict[str, Any],
        responses={401: {"description": "Not signed with the workspace's webhook secret or runtime token."}},
    )
    async def vapi_assistant_request(
        workspace: str,
        request: Request,
        background: BackgroundTasks,
        agent: str | None = Query(None, description="The agent; default: the only Vapi agent in the release."),
        release: int | None = Query(None, description="Pin a release; default: the live one."),
    ) -> dict:
        """Vapi's server URL. For `assistant-request` it returns the agent's assistant id with overrides that carry
        the agent's full instructions: `model` with the system message replaced (read from the assistant with the stored API
        key), and `variableValues.sopops_prompt` for assistants whose system message is `{{sopops_prompt}}`.
        `metadata` and `variableValues` also carry `sopops_release` and `sopops_agent`.

        It also brings sessions back: `end-of-call-report` is stored as a finished session, and `conversation-update`
        and final `transcript` messages stream into it while the call runs (turn them on under Server Messages).
        These and other server messages get an empty 200."""
        body = await verified(request, workspace, "vapi")
        message = body.get("message") or {}
        if message.get("type") != "assistant-request":
            if isinstance(message, dict):
                await run_in_threadpool(vapi_message, store, request.app.state.jobs, workspace, message, background)
            return {}
        call_id = (message.get("call") or {}).get("id")

        def serve() -> dict:
            served = runtime.resolve(workspace, "vapi", agent, release)
            runtime.record_serve(workspace, "vapi", [call_id], served)
            pid = served["platform_ref"].partition(":")[2]
            return {**served, "platform_id": pid, "model": assistants.model_with_prompt(runtime, workspace, pid, served["prompt"])}

        served = await run_in_threadpool(serve)
        tags = {"sopops_release": served["release"], "sopops_agent": served["agent"]}
        overrides: dict[str, Any] = {"variableValues": {"sopops_prompt": served["prompt"], **tags}, "metadata": tags}
        if served["model"] is not None:
            overrides["model"] = served["model"]
        return {"assistantId": served["platform_id"], "assistantOverrides": overrides}

    app.include_router(r)
    return runtime


def push_on_release(runtime: Runtime, background: BackgroundTasks, workspace: str, number: int) -> None:
    """After a release goes live: push it to every push-mode agent's platform, after the response."""
    if runtime.pushes_pending(workspace):
        background.add_task(runtime.push_release, workspace, number)
