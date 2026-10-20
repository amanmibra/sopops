"""Sessions: voice calls and text chats. Ingest takes a token with the ingest scope; reading is open like the rest (v0)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..errors import workspace_id
from ..jobs import Jobs
from ..schemas import (
    Channel,
    CheckResultValue,
    Platform,
    RuleStatus,
    SessionClose,
    SessionDetail,
    SessionEventsIn,
    SessionEventsOut,
    SessionIn,
    SessionIngested,
    SessionOpen,
    SessionPage,
    SessionStatus,
)
from ..store import Store
from ..tokens import INGEST
from .deps import OPEN


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()

    bearer = HTTPBearer(auto_error=False, description="A token with the ingest scope (Settings → Tokens).")

    @r.post(
        "/v1/workspaces/{workspace}/sessions",
        response_model=SessionIngested,
        tags=["sessions"],
        responses={201: {"model": SessionIngested, "description": "Created."}, 401: {"description": "Missing, unknown, expired or revoked token."}, 403: {"description": "The token lacks the ingest scope."}},
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
        store.tokens.check(workspace, f"Bearer {credentials.credentials}" if credentials else None, INGEST)
        done = store.sessions.ingest(workspace, req)
        if done.changed:
            jobs.process_session(done.ref, background)
        response.status_code = 201 if done.created else 200
        return {"id": done.id, "created": done.created, "updated": done.updated, "status": done.status, "warnings": done.warnings}

    # streaming (optional): open a session, append its events as they happen, close it (or post it in full, which wins)

    auth = {401: {"description": "Missing, unknown, expired or revoked token."}, 403: {"description": "The token lacks the ingest scope."}}

    def ingest_token(workspace: str, credentials: HTTPAuthorizationCredentials | None) -> None:
        store.tokens.check(workspace, f"Bearer {credentials.credentials}" if credentials else None, INGEST)

    @r.post("/v1/workspaces/{workspace}/sessions/open", response_model=SessionIngested, tags=["sessions"],
            responses={201: {"model": SessionIngested, "description": "Opened."}, **auth})
    def open_session(workspace: str, req: SessionOpen, response: Response, background: BackgroundTasks,
                     credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        """Open a session to stream its turns and tool calls (optional; see `…/events`). Idempotent on (platform,
        external_id). It isn't checked until it closes: `…/close`, a full post to `…/sessions` (which wins), or no
        events for `SOPOPS_SESSION_IDLE_MINUTES` (default 30; closed as incomplete)."""
        ingest_token(workspace, credentials)
        for ref in store.sessions.close_idle(workspace):
            jobs.process_session(ref, background)
        done = store.sessions.open(workspace, req)
        response.status_code = 201 if done.created else 200
        return {"id": done.id, "created": done.created, "updated": done.updated, "status": done.status, "warnings": done.warnings}

    @r.post("/v1/workspaces/{workspace}/sessions/{platform}/{external_id}/events", response_model=SessionEventsOut, tags=["sessions"],
            responses={404: {"description": "No such session: open it first."}, **auth})
    def append_session_events(workspace: str, platform: str, external_id: str, req: SessionEventsIn,
                              credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        """Append turns and tool calls to an open session. `seq` counts from 0 per session; a resent `seq` is ignored
        and events may arrive in any order. Events for a session that has closed are ignored."""
        ingest_token(workspace, credentials)
        ref, accepted, duplicates = store.sessions.append(workspace, platform, external_id, req.events)
        if accepted:
            jobs.session_event(ref, [e.model_dump(mode="json", exclude_none=True) for e in req.events])
        return {"accepted": accepted, "duplicates": duplicates}

    @r.post("/v1/workspaces/{workspace}/sessions/{platform}/{external_id}/close", response_model=SessionIngested, tags=["sessions"],
            responses={404: {"description": "No such session."}, **auth})
    def close_session(workspace: str, platform: str, external_id: str, background: BackgroundTasks, req: SessionClose | None = None,
                      credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        """Close an open session with what streamed in, and check it. A session that isn't open is left as is."""
        ingest_token(workspace, credentials)
        req = req or SessionClose()
        done = store.sessions.close(workspace, platform, external_id, req.ended_at, req.partial, test=req.test, config=req.config)
        if done.changed:
            jobs.process_session(done.ref, background)
        return {"id": done.id, "created": done.created, "updated": done.updated, "status": done.status, "warnings": done.warnings}

    @r.get("/v1/workspaces/{workspace}/sessions", response_model=SessionPage, dependencies=OPEN, tags=["sessions"])
    def list_sessions(
        workspace: str,
        agent: str | None = Query(None, description="sopc agent id, or the agent as sent."),
        channel: Channel | None = None,
        status: SessionStatus | None = None,
        result: CheckResultValue | None = Query(None, description="The session's worst procedure result."),
        platform: Platform | None = None,
        since: datetime | None = Query(None, description="Started at or after."),
        until: datetime | None = Query(None, description="Started before."),
        cursor: str | None = Query(None, description="`next_cursor` from the previous page."),
        limit: int = Query(50, ge=1, le=200),
        release: int | None = Query(None, description="The release the session ran."),
        procedure: str | None = Query(None, description="Sessions the router considered this procedure for; each item then carries `procedure_check`."),
        procedure_result: CheckResultValue | None = Query(None, description="With a procedure check with this result (of `procedure`, if given)."),
        rule: str | None = Query(None, description="With a result on this rule id."),
        rule_status: list[RuleStatus] | None = Query(None, description="With `rule`: only these statuses (e.g. skipped); alone: a result on any rule with one of them."),
        pattern: str | None = Query(None, description="With a finding in this pattern (a rule pattern includes its sub-patterns)."),
        theme: str | None = Query(None, description="With a finding in a pattern of this theme."),
        finding: bool | None = Query(None, description="With (true) or without (false) any finding."),
        eligible: bool | None = Query(None, description="Only sessions Insights rates (checked or incomplete, with a result); false: the others."),
        suppressed: bool | None = Query(None, description="How `rule`/`rule_status`, `pattern`, `theme` and `finding` treat suppressed findings: false leaves them "
                                        "out (what Insights counts), true matches only them; unset, both."),
    ) -> dict:
        """Sessions, newest first by start time. The drill-down filters are what Insights links to."""
        return store.sessions.list(
            workspace, agent=agent, channel=channel, status=status, result=result, platform=platform, since=since, until=until, cursor=cursor, limit=limit,
            release=release, procedure=procedure, procedure_result=procedure_result, rule=rule, rule_status=rule_status, pattern=pattern, theme=theme, finding=finding, eligible=eligible, suppressed=suppressed,
        )

    @r.get(
        "/v1/workspaces/{workspace}/sessions/stream",
        dependencies=OPEN,
        tags=["sessions"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events: `event: session`, `data: SessionEvent` JSON; "
                         "and `event: repo`, `data: RepoEvent` JSON."}},
    )
    async def stream_sessions(workspace: str, request: Request) -> StreamingResponse:
        """Live session events for the workspace (server-sent events): `{type: created|updated, workspace, id, status}`
        each time a session is ingested or the pipeline updates it, and repository events (`event: repo`: synced from
        GitHub, a sync conflict, a pull request merged, the connection broken). A comment every 15s keeps it open."""
        ws = workspace_id(workspace)
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
                    yield f"event: {'repo' if event.get('topic') == 'repo' else 'session'}\ndata: {json.dumps(event, default=str)}\n\n"
            finally:
                unsubscribe()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.get("/v1/workspaces/{workspace}/sessions/{id}", response_model=SessionDetail, dependencies=OPEN, tags=["sessions"])
    def get_session(workspace: str, id: str) -> dict:
        """One session with its transcript, tool calls and metadata."""
        return store.sessions.get(workspace, id)

    return r
