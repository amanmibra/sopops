"""Sessions with no code in the agent (`sopops_server.capture`): their settings, and the platforms' webhooks.

Under /v1/workspaces/<ws>: `capture` (settings; secrets are write-only), `capture/livekit/webhook` (LiveKit Cloud's
room and participant events, signed with the project's API key) and `capture/elevenlabs/post-call` (ElevenLabs'
post-call transcription webhook, HMAC-signed). Vapi's messages come to its Server URL (`runtime.py`), handled by
`vapi_message` here. `capture/<platform>/backfill` brings the last 7 days of ElevenLabs or Vapi calls in
(`capture.backfill`): a preview, a start, and the latest run's progress.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from ..capture import LIVEKIT_KEY, LIVEKIT_SECRET, POSTCALL_SECRET, Capture, LiveRoomRef
from ..capture import elevenlabs as el
from ..capture import livekit as lk
from ..capture import vapi
from ..capture.backfill import MAX_DAYS, Backfills, from_end_of_call, from_post_call
from ..errors import workspace_id
from ..jobs import Jobs
from ..runtime.service import BadRequest
from ..schemas import SessionEventIn
from ..schemas.capture import (
    BackfillIn,
    BackfillOut,
    BackfillPreview,
    CaptureOut,
    CaptureTestOut,
    ElevenLabsCapture,
    LiveIn,
    LiveKitCapture,
    LiveKitIn,
    PostCallSecretIn,
    WebhookReceipt,
)
from ..schemas.runtime import PushPlatform, SecretInfo
from ..store import Store
from ..tokens import Unauthorized
from .deps import OPEN

log = logging.getLogger("sopops.capture")

WS = "/v1/workspaces/{workspace}"
LIVEKIT_HOOK = "capture/livekit/webhook"
POSTCALL_HOOK = "capture/elevenlabs/post-call"
VAPI_HOOK = "runtime/vapi/assistant-request"
SIGNED = {401: {"description": "Not signed with this workspace's secret."}}


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()
    cap = Capture(store)
    backfills = Backfills(store, jobs)

    # --- settings -----------------------------------------------------------------------------

    def livekit_out(ws: str) -> dict:
        row = cap.setting(ws, "livekit")
        key, secret = cap.secret_info(ws, "livekit", LIVEKIT_KEY), {**cap.secret_info(ws, "livekit", LIVEKIT_SECRET), "hint": None}
        return {
            "connected": bool(row and row.url and key["set"] and secret["set"]), "url": row.url if row else None, "api_key": key, "api_secret": secret,
            "agent_map": row.agent_map if row else {}, "webhook_path": f"/v1/workspaces/{ws}/{LIVEKIT_HOOK}", "last_event_at": row.event_at if row else None,
            "last_attach_at": cap.last_attach_at(ws), "recorder_installed": _recorder_installed(),
        }

    def elevenlabs_out(ws: str) -> dict:
        row = cap.setting(ws, "elevenlabs")
        return {
            "post_call_secret": cap.secret_info(ws, "elevenlabs", POSTCALL_SECRET), "post_call_path": f"/v1/workspaces/{ws}/{POSTCALL_HOOK}",
            "last_post_call_at": row.event_at if row else None, "api_key_set": cap.secret_info(ws, "elevenlabs", "api_key")["set"],
            "live": bool(row and row.live), "live_state": row.live_state if row else None, "live_detail": row.live_detail if row else None,
            "live_checked_at": row.live_checked_at if row else None,
        }

    @r.get(f"{WS}/capture", response_model=CaptureOut, dependencies=OPEN, tags=["capture"])
    def get_capture(workspace: str) -> dict:
        """How sessions reach sopops with no code in the agent: LiveKit, the ElevenLabs post-call webhook and live
        monitoring, and Vapi's server messages. Secrets are never returned."""
        ws = workspace_id(workspace)
        return {"livekit": livekit_out(ws), "elevenlabs": elevenlabs_out(ws),
                "vapi": {"server_url_path": f"/v1/workspaces/{ws}/{VAPI_HOOK}", "server_messages": list(vapi.SERVER_MESSAGES)}}

    @r.put(f"{WS}/capture/livekit", response_model=LiveKitCapture, dependencies=OPEN, tags=["capture"])
    def set_livekit(workspace: str, req: LiveKitIn) -> dict:
        """Connect a LiveKit Cloud project: its URL, an API key and its secret (encrypted with SOPOPS_SECRET_KEY),
        and the agent mapping. Then add the webhook in LiveKit Cloud. 503 without SOPOPS_SECRET_KEY."""
        ws = workspace_id(workspace)
        try:
            url = lk.normalize_url(req.url)
        except lk.BadUrl as e:
            raise BadRequest(str(e)) from None
        for kind, value in ((LIVEKIT_KEY, req.api_key), (LIVEKIT_SECRET, req.api_secret)):
            if value and value.strip():
                cap.set_secret(ws, "livekit", kind, value)
            elif not cap.secret_info(ws, "livekit", kind)["set"]:
                raise BadRequest("enter the API key and its secret")
        fields: dict[str, Any] = {"url": url}
        if req.agent_map is not None:
            fields["agent_map"] = req.agent_map
        cap.update(ws, "livekit", **fields)
        return livekit_out(ws)

    @r.delete(f"{WS}/capture/livekit", status_code=204, dependencies=OPEN, tags=["capture"])
    def delete_livekit(workspace: str) -> Response:
        """Disconnect the LiveKit project: its key, secret and settings are removed, and its webhooks refused."""
        cap.forget(workspace_id(workspace), "livekit", (LIVEKIT_KEY, LIVEKIT_SECRET))
        return Response(status_code=204)

    @r.post(f"{WS}/capture/livekit/test", response_model=CaptureTestOut, dependencies=OPEN, tags=["capture"])
    async def test_livekit(workspace: str) -> dict:
        """List the project's rooms with the stored key (read-only)."""
        ws = workspace_id(workspace)
        row = await run_in_threadpool(cap.setting, ws, "livekit")
        key = await run_in_threadpool(cap.secret, ws, "livekit", LIVEKIT_KEY)
        secret = await run_in_threadpool(cap.secret, ws, "livekit", LIVEKIT_SECRET)
        if not row or not row.url or not key or not secret:
            raise BadRequest("connect a LiveKit project first")
        try:
            from ..capture import livekit_rtc  # optional: pip install 'sopops-server[livekit]'
        except ImportError:
            return {"ok": False, "message": "This server can't reach LiveKit yet. Install it with pip install 'sopops-server[livekit]' and restart."}
        try:
            rooms = await livekit_rtc.test_credentials(row.url, key, secret)
        except Exception as e:  # never echo LiveKit's message: keep secrets out of it
            status = livekit_rtc.twirp_status(e)
            if status in (401, 403):
                return {"ok": False, "message": "LiveKit refused the API key and secret. Check that they're from this project."}
            return {"ok": False, "message": f"Couldn't reach LiveKit at {row.url} ({status or type(e).__name__})."}
        return {"ok": True, "message": f"Connected. The project has {rooms} room{'' if rooms == 1 else 's'} open right now."}

    @r.put(f"{WS}/capture/elevenlabs/post-call-secret", response_model=SecretInfo, dependencies=OPEN, tags=["capture"])
    def set_post_call_secret(workspace: str, req: PostCallSecretIn) -> dict:
        """Store the HMAC secret ElevenLabs shows for the post-call webhook (encrypted). 503 without SOPOPS_SECRET_KEY."""
        ws = workspace_id(workspace)
        cap.set_secret(ws, "elevenlabs", POSTCALL_SECRET, req.value)
        return cap.secret_info(ws, "elevenlabs", POSTCALL_SECRET)

    @r.delete(f"{WS}/capture/elevenlabs/post-call-secret", status_code=204, dependencies=OPEN, tags=["capture"])
    def delete_post_call_secret(workspace: str) -> Response:
        cap.delete_secret(workspace_id(workspace), "elevenlabs", POSTCALL_SECRET)
        return Response(status_code=204)

    @r.put(f"{WS}/capture/elevenlabs/live", response_model=ElevenLabsCapture, dependencies=OPEN, tags=["capture"])
    def set_elevenlabs_live(workspace: str, req: LiveIn) -> dict:
        """Turn Enterprise live monitoring on or off. When on, each call the conversation-initiation webhook serves is
        followed on ElevenLabs' monitor socket while it runs. Where that isn't available, sessions come from the
        post-call webhook, and the state says why."""
        ws = workspace_id(workspace)
        cap.update(ws, "elevenlabs", live=req.on, **({} if req.on else {"live_state": None, "live_detail": None, "live_checked_at": None}))
        return elevenlabs_out(ws)

    # --- webhooks -----------------------------------------------------------------------------

    @r.post(f"{WS}/{LIVEKIT_HOOK}", response_model=WebhookReceipt, tags=["capture"], responses=SIGNED)
    async def livekit_webhook(workspace: str, request: Request) -> dict:
        """LiveKit Cloud's webhook (Settings → Webhooks, signed with the connected API key). An agent joining a room
        starts recording it; the room finishing ends the recording. Other events are acknowledged."""
        ws = workspace_id(workspace)
        raw = await request.body()

        def check() -> None:
            key, secret = cap.secret(ws, "livekit", LIVEKIT_KEY), cap.secret(ws, "livekit", LIVEKIT_SECRET)
            if not key or not secret:
                raise Unauthorized("this workspace has no LiveKit project connected (Settings → Platforms)")
            try:
                lk.verify_webhook(raw, request.headers.get("authorization"), key, secret)
            except lk.BadSignature as e:
                log.warning("LiveKit webhook for %s refused: %s (it must be signed with the API key connected in sopops)", ws, e)
                raise Unauthorized("the webhook isn't signed with the LiveKit API key connected in sopops") from None
            cap.note_event(ws, "livekit")

        await run_in_threadpool(check)
        body = _json_object(raw)
        event, room = body.get("event"), body.get("room") if isinstance(body.get("room"), dict) else {}
        if not room.get("name"):
            return {"status": "ignored"}
        if event == "participant_joined":
            p = body.get("participant") if isinstance(body.get("participant"), dict) else {}
            if not (lk.is_agent_participant(p) or lk.job_id(p.get("identity"))):
                return {"status": "acknowledged"}
            ref = LiveRoomRef(workspace_id=ws, room=room["name"], room_sid=room.get("sid"), agent_identity=p.get("identity"),
                              agent_name=lk.agent_name(p.get("attributes") if isinstance(p.get("attributes"), dict) else None))
            return {"status": "recording" if jobs.watch_livekit_room(ref) != "already" else "already recording"}
        if event == "room_finished":
            jobs.end_livekit_room(ws, room["name"])
            return {"status": "finished"}
        return {"status": "acknowledged"}

    @r.post(f"{WS}/{POSTCALL_HOOK}", response_model=WebhookReceipt, tags=["capture"], responses=SIGNED)
    async def elevenlabs_post_call(workspace: str, request: Request, background: BackgroundTasks) -> dict:
        """ElevenLabs' post-call webhook (transcription), signed with the secret stored here. Each conversation
        becomes a session keyed by its conversation id: the same session a live monitor streamed, which this replaces.
        Other webhook types (audio, failed calls) are acknowledged."""
        ws = workspace_id(workspace)
        raw = await request.body()

        def check() -> None:
            secret = cap.secret(ws, "elevenlabs", POSTCALL_SECRET)
            if not secret:
                raise Unauthorized("this workspace has no ElevenLabs post-call webhook secret (Settings → Platforms)")
            if not el.verify_signature(request.headers.get("elevenlabs-signature"), raw, secret):
                raise Unauthorized("the webhook isn't signed with this workspace's ElevenLabs post-call secret, or it's too old")
            cap.note_event(ws, "elevenlabs")

        await run_in_threadpool(check)
        body = _json_object(raw)
        session = await run_in_threadpool(from_post_call, store, ws, body)
        if session is None:
            return {"status": "ignored"}
        done = await run_in_threadpool(store.sessions.ingest, ws, session)
        if done.changed:
            jobs.process_session(done.ref, background)
        return {"status": "created" if done.created else "updated" if done.updated else "unchanged", "session_id": done.id}

    # --- backfill: the last days of calls, from the platform's API ------------------------------------------------

    @r.get(f"{WS}/capture/{{platform}}/backfill/preview", response_model=BackfillPreview, dependencies=OPEN, tags=["capture"])
    def backfill_preview(workspace: str, platform: PushPlatform, days: int = Query(MAX_DAYS, ge=1, description="Up to 7; more counts as 7.")) -> dict:
        """What a backfill would bring in: each agent on the platform at head, with its finished calls in the window
        (read now with the stored API key), how many are already in sopops, how many are new, and roughly what
        checking them costs. Agents whose calls can't be listed (no key, a refusal) carry the reason."""
        return backfills.preview(workspace, platform, days)

    @r.post(f"{WS}/capture/{{platform}}/backfill", response_model=BackfillOut, status_code=202, dependencies=OPEN, tags=["capture"])
    def start_backfill(workspace: str, platform: PushPlatform, req: BackfillIn | None = None) -> dict:
        """Start bringing the window's calls in, in the background; each new session is checked like one a webhook
        sent. Calls already in sopops are skipped, so running it again only adds what's new. While one runs, this
        returns it."""
        return backfills.start(workspace, platform, (req or BackfillIn()).days)

    @r.get(f"{WS}/capture/{{platform}}/backfill", response_model=BackfillOut | None, dependencies=OPEN, tags=["capture"])
    def get_backfill(workspace: str, platform: PushPlatform) -> dict | None:
        """The latest backfill for the platform and its progress: found, imported, skipped (already in sopops) and
        failed, in all and per agent. Null before the first."""
        return backfills.latest(workspace, platform)

    return r


def vapi_message(store: Store, jobs: Jobs, workspace: str, message: dict, background: BackgroundTasks) -> None:
    """A Vapi server message other than assistant-request (already verified): `end-of-call-report` is ingested as a
    finished session (it replaces what streamed); `conversation-update` and final `transcript` messages stream into
    the open session. Anything else is ignored."""
    kind = str(message.get("type") or "")
    if kind == "end-of-call-report":
        if (session := from_end_of_call(store, workspace, message)) is not None:
            done = store.sessions.ingest(workspace, session)
            if done.changed:
                jobs.process_session(done.ref, background)
        return
    if kind != "conversation-update" and not kind.startswith("transcript"):
        return
    opening = vapi.opening(message)
    if opening is None:
        return
    store.sessions.open(workspace, opening)
    if kind == "conversation-update":
        turns, tool_calls = vapi.conversation(message)
        store.sessions.replace_streamed(workspace, "vapi", opening.external_id, turns, tool_calls)
    elif (turn := vapi.transcript_turn(message)) is not None:
        seq = Capture(store).next_seq(workspace, "vapi", opening.external_id)
        ref, accepted, _ = store.sessions.append(workspace, "vapi", opening.external_id, [SessionEventIn(seq=seq, type="turn", turn=turn)])
        if accepted:
            jobs.session_event(ref, [{"seq": seq, "type": "turn", "turn": turn}])


def _recorder_installed() -> bool:
    try:
        return importlib.util.find_spec("livekit.rtc") is not None and importlib.util.find_spec("livekit.api") is not None
    except ModuleNotFoundError:
        return False


def _json_object(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise BadRequest("the body isn't JSON") from None
    if not isinstance(body, dict):
        raise BadRequest("the body isn't a JSON object")
    return body
