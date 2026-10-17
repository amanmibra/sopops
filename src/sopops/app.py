"""sopops: versioned sopc workspaces over HTTP, and the prompt each agent gets at call start.

Its OpenAPI spec (/openapi.json) is what SDKs are generated from. Set DATABASE_URL for the database
(default sqlite:///sopops.db) and SOPC_BIN for the sopc binary (default: `sopc` on PATH).
There is no authentication yet: run it on a private network.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.routing import APIRoute
from pydantic import BaseModel, create_model

from . import __version__
from . import forms
from .db import database_url
from .schemas import (
    AgentConfig, AgentFields, AgentIn, AgentOut, AgentSaved, AgentsResponse, BlockRequest, ConfigResponse, DraftResponse,
    EditRequest, FetchOut, FileOut, FilesRequest, FilesResponse, GroupConfig, GroupIn, InstructionConfig, InstructionIn,
    IngestTokenCreated, IngestTokenInfo, ItemVersion, LintResponse, MigrateFilesResponse, MigrateRequest, MigrateResponse, NewAgent, NewGroup, NewInstruction,
    NewProcedure, PreviewResponse, ProcedureConfig, ProcedureIn, PublishRequest, PublishResponse, ReleaseDetail,
    ReleaseRequest, ReleaseResponse, ReleaseSummary, RenderResponse, ReplaceRequest, SessionDetail, SessionIn,
    SessionIngested, SessionPage, SessionStatus, SettingsConfig, SettingsIn, SettingsSaved, Channel, Platform,
    ValidateResponse, WorkspaceOut, WriteResponse, WrittenFile,
)
from .events import PgListener
from .jobs import Jobs
from .runtime.routes import push_on_release, register as register_runtime
from .sessions import BadCursor, Unauthorized
from .sopc import Invalid, Sopc, SopcUnavailable, without_build
from .store import Conflict, NotFound, Store


def web_dir() -> Path | None:
    """The built UI (web/dist): SOPOPS_WEB_DIR, else bundled in the package (wheels and Docker), else a clone's web/dist."""
    candidates = [os.environ.get("SOPOPS_WEB_DIR"), Path(__file__).parent / "static", Path(__file__).resolve().parents[2] / "web" / "dist"]
    for c in candidates:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


NO_UI = """<!doctype html><meta charset="utf-8"><title>sopops</title>
<body style="font:15px system-ui;max-width:60ch;margin:60px auto;padding:0 16px">
<h1>sopops</h1><p>The API is running (<a href="/docs">/docs</a>), but the web UI isn't built.
From a clone: <code>pnpm -C web install &amp;&amp; pnpm -C web build</code>, then restart. Or use the Docker image.</p>"""


def _operation_id(route: APIRoute) -> str:
    """Operation ids (and so the generated client's function names) are the route names: `list_workspaces`, `get_agent`."""
    return route.name


# --- app ----------------------------------------------------------------------


def create_app(store: Store, jobs: Jobs | None = None) -> FastAPI:
    jobs = jobs or Jobs(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # On Postgres, session events from every process (a Hatchet worker too) arrive by LISTEN/NOTIFY.
        listener = None
        if store.sessions.postgres:
            url = store.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
            listener = PgListener(url, store.sessions.broker)
            listener.start()
            app.state.pg_listener = listener
        yield
        if listener:
            listener.stop()

    app = FastAPI(
        title="sopops",
        version=__version__,
        description="Versioned sopc workspaces: edit files, release them, and serve each agent its compiled prompt at call start. "
        "Sessions (voice calls and text chats) come in through the ingest endpoints.",
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.jobs = jobs
    runtime = register_runtime(app, store)  # push to platforms, their call-start webhooks, runtime token
    invalid = {422: {"model": ValidateResponse, "description": "The files don't compile; sopc's issues."}}

    @app.exception_handler(Invalid)
    def _invalid(_, exc: Invalid) -> JSONResponse:
        return JSONResponse(status_code=422, content={"valid": False, "issues": [forms.locate(i).to_dict() for i in exc.issues]})

    @app.exception_handler(NotFound)
    def _not_found(_, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    def _conflict(_, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SopcUnavailable)
    def _sopc_unavailable(_, exc: SopcUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Unauthorized)
    def _unauthorized(_, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(BadCursor)
    def _bad_cursor(_, exc: BadCursor) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True}

    v1: list = []  # no authentication yet (v0); keep sopops on a private network

    # stateless

    @app.post("/v1/validate", response_model=ValidateResponse, dependencies=v1, tags=["stateless"])
    def validate(req: FilesRequest) -> dict:
        issues = store.sopc.validate(without_build(req.files))
        return {"valid": not any(i.severity == "error" for i in issues), "issues": [forms.locate(i).to_dict() for i in issues]}

    @app.post("/v1/render", response_model=RenderResponse, responses=invalid, dependencies=v1, tags=["stateless"])
    def render(req: FilesRequest) -> dict:
        compiled = store.sopc.compile(without_build(req.files))
        if not compiled.valid:
            raise Invalid(compiled.issues)
        return {
            "agents": {a: {k: v for k, v in b.items() if k != "blocks"} for a, b in compiled.agents.items()},
            "lock": compiled.lock,
            "warnings": [forms.locate(i).to_dict() for i in compiled.issues],
        }

    @app.post("/v1/migrate", response_model=MigrateFilesResponse, responses=invalid, dependencies=v1, tags=["stateless"])
    def migrate_files(req: FilesRequest) -> dict:
        """Convert a folder in an older sopc format with `sopc migrate`: the v0.0.8 format (bases/, agent targeting),
        or `locked` (v0.0.9), which sopc no longer has. Nothing stored."""
        return store.sopc.migrate(without_build(req.files))

    @app.post("/v1/lint", response_model=LintResponse, responses=invalid, dependencies=v1, tags=["stateless"])
    def lint(req: FilesRequest) -> dict:
        """Duplicated and conflicting instructions in each agent's prompt. Advisory."""
        return {"findings": store.sopc.lint(without_build(req.files))}

    # files

    @app.get("/v1/workspaces", response_model=list[WorkspaceOut], dependencies=v1, tags=["workspaces"])
    def list_workspaces() -> list[dict]:
        return store.workspaces()

    @app.get("/v1/workspaces/{workspace}/files", response_model=FilesResponse, dependencies=v1, tags=["files"])
    def list_files(workspace: str, content: bool = Query(False, description="Include each file's content, e.g. to export to git.")) -> dict:
        """Head: the latest version of every file."""
        return {"workspace": workspace, "files": store.head(workspace, content)}

    @app.post("/v1/workspaces/{workspace}/files", response_model=WriteResponse, responses=invalid, dependencies=v1, tags=["files"])
    def edit_files(workspace: str, req: EditRequest) -> dict:
        """Change some files atomically. Rejected if the result doesn't compile. Nothing goes live until a release."""
        return {"workspace": workspace, "changed": store.edit(workspace, req.changes, req.author, req.note)}

    @app.put("/v1/workspaces/{workspace}/files", response_model=WriteResponse, responses=invalid, dependencies=v1, tags=["files"])
    def replace_files(workspace: str, req: ReplaceRequest) -> dict:
        """Make head exactly these files (e.g. synced from git); files not listed are deleted."""
        return {"workspace": workspace, "changed": store.replace(workspace, without_build(req.files), req.author, req.note)}

    @app.get("/v1/workspaces/{workspace}/files/{path:path}", response_model=FileOut, dependencies=v1, tags=["files"])
    def get_file(workspace: str, path: str, version: int | None = None) -> dict:
        """A file at head, or at `version`."""
        return store.file(workspace, path, version)

    @app.get("/v1/workspaces/{workspace}/history/{path:path}", response_model=list[FileOut], dependencies=v1, tags=["files"])
    def file_history(workspace: str, path: str) -> list[dict]:
        """Every version of a file, newest first, without content."""
        return store.history(workspace, path)

    # items: the structured view the UI's forms edit

    @app.get("/v1/workspaces/{workspace}/config", response_model=ConfigResponse, dependencies=v1, tags=["config"])
    def get_config(workspace: str) -> dict:
        """Head as structured items: settings, agents, shared instructions, procedures (SOPs) and groups.
        422 with `old_format` or `removed_field` issues if the workspace is in an older sopc format: see `…/migrate`."""
        return {"workspace": workspace, **store.items(workspace)}

    def item_routes(kind: str, plural: str, In: type[BaseModel], New: type[BaseModel], Out: type[BaseModel]) -> None:
        """GET/POST {plural}, GET/PUT/DELETE {plural}/{id}, and its history and old versions."""
        path = f"/v1/workspaces/{{workspace}}/config/{plural}"
        what = {"agent": "agent", "instruction": "shared instruction", "procedure": "procedure (SOP)", "group": "group (in sopc.yaml)"}[kind]
        Result = create_model(f"{Out.__name__}Saved", item=(Out, ...), changed=(list[WrittenFile], ...))

        def save(workspace: str, id: str, req: BaseModel, create: bool) -> dict:
            data = req.model_dump(exclude={"author", "note", "id"})
            item, changed = store.put_item(workspace, kind, id, data, req.author, req.note, create=create)
            return {"item": item, "changed": changed}

        def list_items(workspace: str) -> list[dict]:
            return store.items(workspace)[plural]

        def create(workspace: str, req) -> dict:
            return save(workspace, req.id, req, create=True)

        def get(workspace: str, id: str) -> dict:
            return store.item(workspace, kind, id)

        def put(workspace: str, id: str, req) -> dict:
            return save(workspace, id, req, create=False)

        def delete(workspace: str, id: str, author: str | None = None, note: str | None = None) -> dict:
            return {"workspace": workspace, "changed": store.delete_item(workspace, kind, id, author, note)}

        def history(workspace: str, id: str) -> list[dict]:
            return store.item_history(workspace, kind, id)

        def version(workspace: str, id: str, version: int, file: str | None = Query(None, description="From history; for a procedure that changed format.")) -> dict:
            return store.item_version(workspace, kind, id, version, file)

        create.__annotations__ = {"workspace": str, "req": New, "return": dict}
        put.__annotations__ = {"workspace": str, "id": str, "req": In, "return": dict}
        routes = [
            ("GET", "", list_items, list[Out], f"Every {what} at head.", {}),
            ("POST", "", create, Result, f"Create a {what}. Rejected if the result doesn't compile; 409 if it exists.", {**invalid, 409: {"description": "Already exists."}}),
            ("GET", "/{id}", get, Out, f"One {what} at head.", {}),
            ("PUT", "/{id}", put, Result, f"Create or replace a {what}. Rejected if the result doesn't compile. Nothing goes live until a release.", invalid),
            ("DELETE", "/{id}", delete, WriteResponse, f"Delete a {what}. {DELETES[kind]}", invalid),
            ("GET", "/{id}/history", history, list[ItemVersion], f"Every version of a {what}, newest first.", {}),
            ("GET", "/{id}/versions/{version}", version, dict[str, Any], f"An older version of a {what}, as fields.", {}),
        ]
        for method, suffix, fn, model, doc, responses in routes:
            app.add_api_route(
                path + suffix, fn, methods=[method], response_model=model, description=doc, responses=responses,
                dependencies=v1, tags=["config"], name=OPS[fn.__name__].format(kind=kind, plural=plural), status_code=201 if method == "POST" else 200,
            )

    OPS = {
        "list_items": "config_list_{plural}",
        "create": "config_create_{kind}",
        "get": "config_get_{kind}",
        "put": "config_put_{kind}",
        "delete": "config_delete_{kind}",
        "history": "config_{kind}_history",
        "version": "config_{kind}_version",
    }
    DELETES = {
        "agent": "Nothing else changes.",
        "instruction": "It's taken out of every agent and group that lists it.",
        "procedure": "It's taken out of every agent and group that lists it.",
        "group": "Wherever it's listed, its blocks are listed in its place, so no prompt changes.",
    }
    item_routes("agent", "agents", AgentIn, NewAgent, AgentConfig)
    item_routes("instruction", "instructions", InstructionIn, NewInstruction, InstructionConfig)
    item_routes("procedure", "procedures", ProcedureIn, NewProcedure, ProcedureConfig)
    item_routes("group", "groups", GroupIn, NewGroup, GroupConfig)

    @app.post("/v1/workspaces/{workspace}/config/agents/{id}/blocks", response_model=AgentSaved, responses={**invalid, 409: {"description": "The agent already uses it."}}, dependencies=v1, tags=["config"])
    def add_block(workspace: str, id: str, req: BlockRequest) -> dict:
        """Add a shared instruction, procedure or group at the end of the agent's blocks. 409 if the agent already uses it."""
        item, changed = store.add_block(workspace, id, req.block, req.author, req.note)
        return {"item": item, "changed": changed}

    @app.delete("/v1/workspaces/{workspace}/config/agents/{id}/blocks/{block}", response_model=AgentSaved, responses={**invalid, 409: {"description": "The agent gets it through a group."}}, dependencies=v1, tags=["config"])
    def remove_block(workspace: str, id: str, block: str, author: str | None = None, note: str | None = None) -> dict:
        """Take a block out of the agent's blocks. 409 if the agent gets it through a group."""
        item, changed = store.remove_block(workspace, id, block, author, note)
        return {"item": item, "changed": changed}

    @app.post("/v1/workspaces/{workspace}/config/agents/{id}/preview", response_model=PreviewResponse, dependencies=v1, tags=["config"])
    def preview_agent(workspace: str, id: str, req: AgentFields) -> dict:
        """The prompt this agent would get with these fields, compiled with the rest of head. Saves nothing."""
        return store.preview_agent(workspace, id, req.model_dump())

    @app.post("/v1/workspaces/{workspace}/migrate", response_model=MigrateResponse, responses=invalid, dependencies=v1, tags=["config"])
    def migrate(workspace: str, req: MigrateRequest | None = None) -> dict:
        """Convert a workspace stored in an older sopc format with `sopc migrate`: the v0.0.8 format, or
        `locked` (v0.0.9), which sopc no longer has. Returns the plan;
        with `apply`, writes the converted files as a draft (publish to release them)."""
        req = req or MigrateRequest()
        return store.migrate(workspace, req.apply, req.author, req.note)

    @app.get("/v1/workspaces/{workspace}/config/settings", response_model=SettingsConfig, dependencies=v1, tags=["config"])
    def get_settings(workspace: str) -> dict:
        return store.items(workspace)["settings"]

    @app.put("/v1/workspaces/{workspace}/config/settings", response_model=SettingsSaved, responses=invalid, dependencies=v1, tags=["config"])
    def put_settings(workspace: str, req: SettingsIn) -> dict:
        item, changed = store.put_item(workspace, "settings", "settings", req.model_dump(exclude={"author", "note"}), req.author, req.note)
        return {"item": item, "changed": changed}

    @app.get("/v1/workspaces/{workspace}/config/settings/history", response_model=list[ItemVersion], dependencies=v1, tags=["config"])
    def settings_history(workspace: str) -> list[dict]:
        return store.item_history(workspace, "settings", "settings")

    @app.get("/v1/workspaces/{workspace}/config/settings/versions/{version}", response_model=dict[str, Any], dependencies=v1, tags=["config"])
    def settings_version(workspace: str, version: int) -> dict:
        return store.item_version(workspace, "settings", "settings", version)

    # releases

    @app.get("/v1/workspaces/{workspace}/draft", response_model=DraftResponse, dependencies=v1, tags=["releases"])
    def draft(workspace: str) -> dict:
        """Unpublished changes: head compiled and compared with the current release."""
        return store.draft(workspace)

    @app.post("/v1/workspaces/{workspace}/releases", response_model=ReleaseResponse, responses=invalid, dependencies=v1, tags=["releases"])
    def create_release(workspace: str, background: BackgroundTasks, req: ReleaseRequest | None = None) -> dict:
        """Release head and serve it. Agents set to push get it on their platform, after the response."""
        req = req or ReleaseRequest()
        release, created = store.release(workspace, req.author, req.note)
        if created:
            push_on_release(runtime, background, workspace, release["number"])
        return {"release": release, "created": created}

    @app.get("/v1/workspaces/{workspace}/releases", response_model=list[ReleaseSummary], dependencies=v1, tags=["releases"])
    def list_releases(workspace: str) -> list[dict]:
        return store.releases(workspace)

    @app.get("/v1/workspaces/{workspace}/releases/{number}", response_model=ReleaseDetail, dependencies=v1, tags=["releases"])
    def get_release(workspace: str, number: int) -> dict:
        """A release with every agent's prompt. Doesn't log a fetch."""
        rel = store.get_release(workspace, number)
        return {**rel, "agents": {a: _released(b) for a, b in rel["build"]["agents"].items()}}

    @app.post("/v1/workspaces/{workspace}/releases/{number}/activate", response_model=WorkspaceOut, dependencies=v1, tags=["releases"])
    def activate(workspace: str, number: int, background: BackgroundTasks) -> dict:
        """Serve this release, e.g. to roll back. Agents set to push get it on their platform, after the response."""
        out = store.activate(workspace, number)
        push_on_release(runtime, background, workspace, number)
        return out

    @app.post("/v1/workspaces/{workspace}/publish", response_model=PublishResponse, responses=invalid, dependencies=v1, tags=["releases"])
    def publish(workspace: str, req: PublishRequest, background: BackgroundTasks) -> dict:
        """Replace head with these files and release it: the CI path for git workspaces."""
        release, created, changed = store.publish(workspace, without_build(req.files), req.author, req.note)
        if created:
            push_on_release(runtime, background, workspace, release["number"])
        return {"release": release, "created": created, "changed": changed}

    # serving. Prompt fetches take the workspace's runtime token (its ingest token) once it has one.

    runtime_bearer = HTTPBearer(auto_error=False, description="The workspace's runtime token (Settings → Ingest token), once it has one.")

    def runtime_token(workspace: str, credentials: HTTPAuthorizationCredentials | None = Depends(runtime_bearer)) -> None:
        runtime.check_fetch(workspace, f"Bearer {credentials.credentials}" if credentials else None)

    fetch_auth = [Depends(runtime_token)]

    @app.get("/v1/workspaces/{workspace}/agents", response_model=AgentsResponse, dependencies=v1, tags=["serving"])
    def list_agents(workspace: str, release: int | None = None) -> dict:
        rel = store.get_release(workspace, release)
        agents = [{"agent": a, "platform_ref": b["platform_ref"], "hash": b["hash"]} for a, b in sorted(rel["build"]["agents"].items())]
        return {"workspace": workspace, "release": rel["number"], "agents": agents}

    @app.get("/v1/workspaces/{workspace}/agents/{agent}", response_model=AgentOut, dependencies=fetch_auth, tags=["serving"])
    def get_agent(workspace: str, agent: str, release: int | None = Query(None, description="Pin a release (e.g. for A/B); default: current.")) -> dict:
        """What to load at call start. `agent` is the sopc id or platform ref (`livekit:tonys-pizza`). Logs a fetch."""
        served = store.agent(workspace, agent, release)
        store.log_fetch(workspace, served["agent"], served["release"], served["hash"])
        return {"agent": served["agent"], "release": served["release"], **_released(served)}

    @app.get(
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

    @app.get("/v1/workspaces/{workspace}/agents/{agent}/sops/{sop_id}", response_model=dict[str, Any], dependencies=fetch_auth, tags=["serving"])
    def get_sop(workspace: str, agent: str, sop_id: str, release: int | None = None) -> dict:
        """What the `get_sop` tool returns for SOPs with `delivery: auto | tool`."""
        served = store.agent(workspace, agent, release)
        if sop_id not in served["sops"]:
            raise NotFound(f"SOP '{sop_id}' is not tool-delivered to '{served['agent']}'")
        return served["sops"][sop_id]

    @app.get("/v1/workspaces/{workspace}/fetches", response_model=list[FetchOut], dependencies=v1, tags=["serving"])
    def list_fetches(workspace: str, agent: str | None = Query(None, description="sopc agent id."), limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        """Which release and prompt hash each agent was served, newest first."""
        return store.fetches(workspace, agent, limit)

    # sessions: voice calls and text chats. Ingest takes the workspace's ingest token; reading is open like the rest (v0).

    bearer = HTTPBearer(auto_error=False, description="The workspace's ingest token (Settings → Ingest token).")

    @app.post(
        "/v1/workspaces/{workspace}/sessions",
        response_model=SessionIngested,
        tags=["sessions"],
        responses={201: {"model": SessionIngested, "description": "Created."}, 401: {"description": "Missing or wrong ingest token."}},
    )
    def ingest_session(
        workspace: str,
        req: SessionIn,
        response: Response,
        background: BackgroundTasks,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> dict:
        """Ingest a finished session in the canonical format: an idempotent upsert on (platform, external_id).
        201 when created; 200 when it already existed (updated if its content changed, else untouched).
        Starts `process_session` for a new or changed session."""
        store.sessions.check_token(workspace, f"Bearer {credentials.credentials}" if credentials else None)
        done = store.sessions.ingest(workspace, req)
        if done.changed:
            jobs.process_session(done.ref, background)
        response.status_code = 201 if done.created else 200
        return {"id": done.id, "created": done.created, "updated": done.updated, "status": done.status, "warnings": done.warnings}

    @app.get("/v1/workspaces/{workspace}/sessions", response_model=SessionPage, dependencies=v1, tags=["sessions"])
    def list_sessions(
        workspace: str,
        agent: str | None = Query(None, description="sopc agent id, or the agent as sent."),
        channel: Channel | None = None,
        status: SessionStatus | None = None,
        platform: Platform | None = None,
        since: datetime | None = Query(None, description="Started at or after."),
        until: datetime | None = Query(None, description="Started before."),
        cursor: str | None = Query(None, description="`next_cursor` from the previous page."),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict:
        """Sessions, newest first by start time."""
        return store.sessions.list(
            workspace, agent=agent, channel=channel, status=status, platform=platform, since=since, until=until, cursor=cursor, limit=limit
        )

    @app.get(
        "/v1/workspaces/{workspace}/sessions/stream",
        dependencies=v1,
        tags=["sessions"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events: `event: session`, `data: SessionEvent` JSON."}},
    )
    async def stream_sessions(workspace: str, request: Request) -> StreamingResponse:
        """Live session events for the workspace (server-sent events): `{type: created|updated, workspace, id, status}`
        each time a session is ingested or the pipeline updates it. A comment every 15s keeps the connection open."""
        from .store import _ws

        ws = _ws(workspace)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

        def put(event: dict) -> None:
            if not queue.full():
                queue.put_nowait(event)

        unsubscribe = store.sessions.broker.subscribe(ws, lambda e: loop.call_soon_threadsafe(put, e))

        async def events():
            try:
                yield "retry: 3000\n: connected\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: session\ndata: {json.dumps(event)}\n\n"
            finally:
                unsubscribe()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/v1/workspaces/{workspace}/sessions/{id}", response_model=SessionDetail, dependencies=v1, tags=["sessions"])
    def get_session(workspace: str, id: str) -> dict:
        """One session with its transcript, tool calls and metadata."""
        return store.sessions.get(workspace, id)

    @app.get("/v1/workspaces/{workspace}/ingest-token", response_model=IngestTokenInfo, dependencies=v1, tags=["sessions"])
    def get_ingest_token(workspace: str) -> dict:
        """Whether the workspace has an ingest token, and its first characters. The token itself is never shown again."""
        return store.sessions.token_info(workspace)

    @app.post("/v1/workspaces/{workspace}/ingest-token", response_model=IngestTokenCreated, dependencies=v1, tags=["sessions"])
    def rotate_ingest_token(workspace: str) -> dict:
        """Create the workspace's ingest token, or replace it (the old one stops working). Returned once."""
        return store.sessions.rotate_token(workspace)

    @app.delete("/v1/workspaces/{workspace}/ingest-token", status_code=204, dependencies=v1, tags=["sessions"])
    def revoke_ingest_token(workspace: str) -> Response:
        """Delete the ingest token: ingest is refused until a new one is made."""
        store.sessions.revoke_token(workspace)
        return Response(status_code=204)

    # the web UI: built assets, and index.html for every other path so the client-side router can take it
    ui = web_dir()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path == "v1" or path.startswith("v1/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if ui is None:
            return HTMLResponse(NO_UI)
        if path:
            file = (ui / path).resolve()
            if file.is_relative_to(ui.resolve()) and file.is_file():
                immutable = path.startswith("assets/")
                return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        return FileResponse(ui / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _released(b: dict) -> dict:
    return {
        "platform_ref": b["platform_ref"],
        "hash": b["hash"],
        "prompt": b["prompt"],
        "tools": b["tools"],
        "tool_sops": sorted(b["sops"]),
        "components": b["components"],
    }


default_database_url = database_url  # the name before db.py


def app_from_env() -> FastAPI:
    return create_app(Store(default_database_url(), Sopc()))

