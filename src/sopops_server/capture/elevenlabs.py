"""ElevenLabs: the post-call webhook (each finished conversation) and, on Enterprise, the monitor socket (each
conversation while it runs).

Post-call: `{"type": "post_call_transcription", "data": {agent_id, conversation_id, status, transcript, metadata,
conversation_initiation_client_data}}`, signed like the conversation-initiation webhook (`ElevenLabs-Signature:
t=<secs>,v0=<hex HMAC-SHA256(secret, "<t>.<body>")>`) with the secret ElevenLabs shows when the webhook is made.

Monitor: `wss://api.elevenlabs.io/v1/convai/conversations/<id>/monitor` with the workspace's API key (`xi-api-key`,
ElevenLabs Agents Write). Enterprise only, the agent needs Monitoring on (Advanced), and the conversation must have
started, so it's tried a few times after the initiation webhook. It replays about the last 100 events on connect, so
events are deduplicated. A refusal (an HTTP 401/403 on connect, or close code 1008) means it isn't available for this
workspace or agent: the state is shown in Settings and the post-call webhook brings the session instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .. import configs
from ..pipeline.types import IDLE_INCOMPLETE, SessionRef
from ..runtime.platforms import base_url
from ..schemas import SessionEventIn, SessionIn, SessionOpen
from . import POSTCALL_SECRET, Capture, LiveConversationRef

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops_server.capture.elevenlabs")

PLATFORM = "elevenlabs"
MONITOR_PATH = "/v1/convai/conversations/{id}/monitor"
POLICY_VIOLATION = 1008
CONNECT_TRIES = 8  # the initiation webhook comes before the conversation starts
CONNECT_WAIT_S = 1.5
# With a post-call webhook set up, a monitored session stays open this long after the socket ends, so the full
# post (with tool calls) is what gets checked, once; without one, it closes right away.
POSTCALL_GRACE_S = float(os.environ.get("SOPOPS_POSTCALL_GRACE_SECONDS", "120"))


# --- post-call webhook ---------------------------------------------------------------------------

SIGNATURE_TOLERANCE_S = 30 * 60  # as ElevenLabs' own SDK checks


def verify_signature(header: str | None, raw: bytes, secret: str, now: float | None = None, tolerance_s: float = SIGNATURE_TOLERANCE_S) -> bool:
    """`ElevenLabs-Signature: t=<unix secs>,v0=<hex HMAC-SHA256(secret, "<t>.<body>")>`, with `t` within the tolerance."""
    try:
        parts = dict(p.strip().split("=", 1) for p in (header or "").split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((time.time() if now is None else now) - t) > tolerance_s:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts.get("v0", ""), want)


def post_call(body: dict) -> SessionIn | None:
    """A `post_call_transcription` webhook as a finished session; None for other types (audio, failures)."""
    if body.get("type") != "post_call_transcription":
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if not data.get("conversation_id"):
        return None
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    init = data.get("conversation_initiation_client_data") if isinstance(data.get("conversation_initiation_client_data"), dict) else {}
    dyn = init.get("dynamic_variables") if isinstance(init.get("dynamic_variables"), dict) else {}
    turns, tool_calls = convert(data.get("transcript") or [])
    started = meta.get("start_time_unix_secs")
    duration = meta.get("call_duration_secs")
    ended = started + duration if isinstance(started, (int, float)) and isinstance(duration, (int, float)) else None
    try:
        release = int(dyn["sopops_release"]) if dyn.get("sopops_release") is not None else None
    except (TypeError, ValueError):
        release = None
    keep = {
        "termination_reason": meta.get("termination_reason"),
        "phone_call": meta.get("phone_call"),  # its call_sid links a phone call to the prompt served for it
        "call_successful": (data.get("analysis") or {}).get("call_successful") if isinstance(data.get("analysis"), dict) else None,
    }
    version = data.get("version_id") or meta.get("version_id")
    return SessionIn(
        platform=PLATFORM, external_id=str(data["conversation_id"]), agent=dyn.get("sopops_agent") or data.get("agent_id"), release=release,
        version_ref=str(version) if version else None, started_at=started, ended_at=ended, partial=data.get("status") == "failed",
        turns=turns, tool_calls=tool_calls,
        metadata={**{k: v for k, v in keep.items() if v is not None}, **{k: v for k, v in dyn.items() if k.startswith(configs.PREFIX) and k not in ("sopops_release", "sopops_agent")}},
    )


def call_override(body: dict) -> dict:
    """A conversation's `conversation_config_override` (its own voice or LLM), from a post-call webhook."""
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    init = data.get("conversation_initiation_client_data") if isinstance(data.get("conversation_initiation_client_data"), dict) else {}
    o = init.get("conversation_config_override")
    return o if isinstance(o, dict) else {}


def convert(transcript: list[Any]) -> tuple[list[dict], list[dict]]:
    """ElevenLabs transcript entries to sopops (turns, tool_calls). A tool call is also a `tool` turn."""
    turns: list[dict] = []
    calls: dict[str, dict] = {}
    for e in transcript:
        if not isinstance(e, dict):
            continue
        offset = e.get("time_in_call_secs")
        text = e.get("message")
        if isinstance(text, str) and text.strip():
            turns.append({"role": e.get("role") or "user", "text": text, "offset_s": offset, **({"interrupted": True} if e.get("interrupted") else {})})
        for i, tc in enumerate(e.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            name, args = tc.get("tool_name") or "tool", _json(tc.get("params_as_json"))
            cid = tc.get("request_id") or f"{offset}:{i}"
            turns.append({"role": "tool", "text": f"{name}({tc.get('params_as_json') or ''})", "name": name, "offset_s": offset})
            calls[cid] = {"name": name, "arguments": args, "call_id": cid, "offset_s": offset}
        for tr in e.get("tool_results") or []:
            if not isinstance(tr, dict):
                continue
            cid = tr.get("request_id")
            call = calls.get(cid) or calls.setdefault(cid or f"result:{len(calls)}", {"name": tr.get("tool_name") or "tool", "call_id": cid, "offset_s": offset})
            call.update(result=_json(tr.get("result_value")), is_error=bool(tr.get("is_error", False)))
    return turns, list(calls.values())


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# --- the monitor socket --------------------------------------------------------------------------


@dataclass
class Frame:
    kind: str  # turn | tool | other
    key: str = ""
    role: str = ""
    text: str = ""
    tool: dict | None = None


def parse_frame(raw: str | bytes) -> Frame:
    """One monitor frame. Never raises on its shape."""
    try:
        p = json.loads(raw)
    except (TypeError, ValueError):
        return Frame("other")
    if not isinstance(p, dict):
        return Frame("other")
    kind = p.get("type")
    if kind == "user_transcript":
        ev = _obj(p, "user_transcription_event")
        text = str(ev.get("user_transcript") or "").strip()
        return Frame("turn", f"user:{ev.get('event_id', text)}", "user", text)
    if kind == "agent_response":
        ev = _obj(p, "agent_response_event")
        text = str(ev.get("agent_response") or "").strip()
        return Frame("turn", f"agent:{ev.get('event_id', text)}", "agent", text)
    if kind in ("client_tool_call", "agent_tool_response"):
        ev = _obj(p, kind)
        name = ev.get("tool_name") or "tool"
        cid = ev.get("tool_call_id") or name
        tool = {"name": name, "call_id": cid, **({"arguments": ev["parameters"]} if "parameters" in ev else {}),
                **({"is_error": bool(ev["is_error"])} if "is_error" in ev else {})}
        return Frame("tool", f"{kind}:{cid}", tool=tool)
    return Frame("other")


def _obj(p: dict, key: str) -> dict:
    v = p.get(key)
    return v if isinstance(v, dict) else {}


class Unavailable(Exception):
    """Monitoring refused for good (not Enterprise, Monitoring off for the agent, or the key lacks the scope)."""


def monitor_url(conversation_id: str) -> str:
    base = os.environ.get("SOPOPS_ELEVENLABS_WS_URL") or base_url("elevenlabs").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return base.rstrip("/") + MONITOR_PATH.format(id=conversation_id)


async def watch_conversation(store: Store, ref: LiveConversationRef, on_closed: Callable[[SessionRef], Any], *,
                             connect: Callable[..., Any] = websockets.connect, sleep: Callable[[float], Any] = asyncio.sleep,
                             grace_s: float | None = None) -> dict:
    """Follow one conversation on the monitor socket and stream its turns into a session; `on_closed` gets the
    session once it closes (to check it). Opens the session on the first event, so a refused monitor leaves
    nothing behind and the post-call webhook brings the session instead."""
    cap = Capture(store)
    ws = ref.workspace_id
    key = await asyncio.to_thread(cap.secret, ws, PLATFORM, "api_key")
    if not key:
        await asyncio.to_thread(cap.note_live, ws, "unavailable", "Add an ElevenLabs API key above. Live monitoring uses it.")
        return {"status": "unavailable", "turns": 0}
    url, seen, seq = monitor_url(ref.conversation_id), set(), 0
    opened = False
    ended_normally = False
    tries = 0
    while True:
        try:
            async with connect(url, additional_headers={"xi-api-key": key}, open_timeout=10) as sock:
                async for raw in sock:
                    f = parse_frame(raw)
                    if f.kind == "other" or f.key in seen or (f.kind == "turn" and not f.text):
                        continue
                    seen.add(f.key)
                    if not opened:  # on the first event: a socket ElevenLabs closes right away (1008) leaves nothing behind
                        await asyncio.to_thread(cap.note_live, ws, "on", None)
                        await asyncio.to_thread(_open, store, ref)
                        opened = True
                    if f.kind == "turn":
                        event = SessionEventIn(seq=seq, type="turn", turn={"role": f.role, "text": f.text, "at": time.time()})
                    else:
                        event = SessionEventIn(seq=seq, type="tool_call", tool_call={**(f.tool or {}), "at": time.time()})
                    seq += 1
                    await asyncio.to_thread(store.sessions.append, ws, PLATFORM, ref.conversation_id, [event])
            ended_normally = True
            break
        except InvalidStatus as e:
            status = e.response.status_code
            if status in (401, 403):
                return await _unavailable(cap, ref, f"ElevenLabs refused the monitor ({status}). It needs an Enterprise plan and an API key with Agents Write.")
            if status != 404 or tries >= CONNECT_TRIES:  # 404: the conversation hasn't started yet
                return await _unavailable(cap, ref, f"ElevenLabs answered {status} to the monitor.", note=status != 404)
        except ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else None
            if code == POLICY_VIOLATION:
                return await _unavailable(cap, ref, "ElevenLabs closed the monitor: turn on Monitoring in the agent's Advanced settings. It's on Enterprise plans only.")
            if code in (1000, 1001):
                ended_normally = True
                break
        except (OSError, asyncio.TimeoutError) as e:
            log.info("monitor for %s: couldn't connect: %s", ref.conversation_id, type(e).__name__)
        tries += 1
        if tries > CONNECT_TRIES:
            return await _unavailable(cap, ref, "Couldn't keep the monitor connected.", note=False)
        await sleep(CONNECT_WAIT_S * min(tries, 4))
    if opened:
        await _finish(store, cap, ref, ended_normally, grace_s, sleep, on_closed)
    return {"status": "done", "turns": seq}


def _open(store: Store, ref: LiveConversationRef) -> None:
    store.sessions.open(ref.workspace_id, SessionOpen(platform=PLATFORM, external_id=ref.conversation_id, agent=ref.agent, release=ref.release,
                                                      metadata={"source": "elevenlabs-monitor"}))


async def _finish(store: Store, cap: Capture, ref: LiveConversationRef, complete: bool, grace_s: float | None, sleep: Callable[[float], Any],
                  on_closed: Callable[[SessionRef], Any]) -> None:
    """Close the streamed session, after a grace for the post-call webhook when one is set up (it wins, once)."""
    has_postcall = await asyncio.to_thread(cap.secret_info, ref.workspace_id, PLATFORM, POSTCALL_SECRET)
    if has_postcall["set"]:
        await sleep(POSTCALL_GRACE_S if grace_s is None else grace_s)
    done = await asyncio.to_thread(store.sessions.close, ref.workspace_id, PLATFORM, ref.conversation_id, None, not complete, None if complete else IDLE_INCOMPLETE)
    if done.changed:
        await asyncio.to_thread(on_closed, done.ref)


async def _unavailable(cap: Capture, ref: LiveConversationRef, detail: str, note: bool = True) -> dict:
    """Give up quietly: the state goes to Settings (unless it's about this call only), and the post-call webhook brings
    the session. One that opened and then dropped stays open for it, else closes as incomplete when idle."""
    log.info("monitor for %s in %s: %s", ref.conversation_id, ref.workspace_id, detail)
    if note:
        await asyncio.to_thread(cap.note_live, ref.workspace_id, "unavailable", detail)
    return {"status": "unavailable", "turns": 0}
