"""LiveKit with no code in the agent: verify LiveKit Cloud's webhooks, and record a room by joining it.

LiveKit has no transcript API or webhook. A LiveKit agent publishes its live transcript into the room as text
streams on `lk.transcription` (older agents: transcription events), for the caller's speech and its own, with the
transcribed participant as the sender. So when a webhook says an agent joined a room, sopops joins it as a hidden,
subscribe-only participant, reads those streams, and streams each finished segment into a session. Tool calls aren't
in the room, so they aren't recorded this way.

- A caller's segment is re-sent whole on each update; only the one marked `lk.transcription_final` is kept. The
  agent's own segment is one stream that the Node SDK never marks final, so an agent stream that ends is kept.
  Each `lk.segment_id` is kept once.
- The session is keyed by the agent's job id (its participant identity is `agent-<job id>`), as the SDK's
  `attach()` keys it, so an agent that also uses the SDK makes one session, not two. Else the room's sid.
- The agent is found by the name it was dispatched by, else `{"sopops": {"agent": ...}}` in job or room metadata,
  else the workspace's mapping (see `Capture.livekit_agent`).
- The call ends when every agent, or every caller, has been gone for a grace period (the recorder itself keeps
  the room open, so waiting for the room to close isn't enough), or the room closes. Then the session closes and is
  checked. Any other way out (our connection dropping) leaves it open: it closes as incomplete when idle.

Everything that touches LiveKit's SDK is in `livekit_rtc` (`pip install 'sopops-server[livekit]'`); this module takes it as
a `link`, so the loop is tested with a fake room.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable, Protocol
from urllib.parse import urlsplit

import jwt

from ..schemas import SessionEventIn, SessionOpen
from . import LIVEKIT_KEY, LIVEKIT_SECRET, Capture, LiveRoomRef

if TYPE_CHECKING:
    from ..pipeline.types import SessionRef
    from ..store import Store

log = logging.getLogger("sopops_server.capture.livekit")

PLATFORM = "livekit"
TRANSCRIPTION_TOPIC = "lk.transcription"
SEGMENT_ID = "lk.segment_id"
FINAL = "lk.transcription_final"
AGENT_NAME_ATTRIBUTES = ("lk.agent_name", "lk.agent.name")
RECORDER_PREFIX = "sopops-recorder"

GRACE_S = 20.0  # a side gone this long ends the call (an agent restarting, a caller reconnecting, rejoin sooner)
POLL_S = 1.0
MAX_CALL_S = 4 * 60 * 60
MAX_SEGMENT_CHARS = 8_000  # any participant can publish on the topic: bound what a room can make us hold
MAX_OPEN_STREAMS = 16
DRAIN_S = 5.0

_CLOUD_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.livekit\.cloud$")


class BadSignature(Exception):
    """A webhook that didn't verify. The reason is for the log only."""


class BadUrl(ValueError):
    pass


def any_host() -> bool:
    """`SOPOPS_LIVEKIT_ANY_HOST=true` allows a self-hosted LiveKit server's URL, not only LiveKit Cloud's."""
    return os.environ.get("SOPOPS_LIVEKIT_ANY_HOST", "").strip().lower() in ("1", "true", "yes", "on")


def normalize_url(raw: str) -> str:
    """`https://<project>.livekit.cloud` from the project URL in either form (the dashboard shows `wss://`). sopops
    calls this host, so only LiveKit Cloud is accepted unless SOPOPS_LIVEKIT_ANY_HOST is set."""
    value = (raw or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in ("https", "wss", "http", "ws"):
        raise BadUrl("the project URL starts with wss:// or https://")
    host = (parts.hostname or "").lower()
    if any_host():
        if not host:
            raise BadUrl("the project URL has no host")
        secure = parts.scheme in ("https", "wss")
        return f"{'https' if secure else 'http'}://{host}{f':{parts.port}' if parts.port else ''}"
    if parts.scheme not in ("https", "wss") or not _CLOUD_HOST.match(host) or parts.port or parts.username or parts.path not in ("", "/") or parts.query:
        raise BadUrl("use your LiveKit Cloud project URL, like wss://my-project.livekit.cloud")
    return f"https://{host}"


def rtc_url(url: str) -> str:
    """The `wss://` form a room connection takes."""
    return url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def verify_webhook(body: bytes, authorization: str | None, api_key: str, api_secret: str) -> None:
    """LiveKit signs each webhook with a JWT in `Authorization` (HS256 with the API secret, issued by the API key)
    that carries the body's sha256 (base64). Both are checked, as LiveKit's own WebhookReceiver does."""
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise BadSignature("no Authorization header")
    try:
        claims = jwt.decode(token, api_secret, algorithms=["HS256"], leeway=60, options={"verify_aud": False})
    except jwt.PyJWTError as e:
        raise BadSignature(type(e).__name__) from None
    if claims.get("iss") != api_key:
        raise BadSignature("signed by another API key")
    want = base64.b64encode(hashlib.sha256(body).digest()).decode()
    if not hmac.compare_digest(str(claims.get("sha256", "")), want):
        raise BadSignature("the body doesn't match the signature")


def is_agent_participant(p: dict) -> bool:
    """A webhook's `participant` that LiveKit marks as an agent (`kind`: `AGENT`, or 4 as a number)."""
    return p.get("kind") in ("AGENT", 4, "4")


def agent_name(attributes: dict | None) -> str | None:
    attributes = attributes or {}
    return next((attributes[k] for k in AGENT_NAME_ATTRIBUTES if attributes.get(k)), None)


def job_id(identity: str | None) -> str | None:
    """A LiveKit agent joins as `agent-<job id>` by default."""
    return identity[len("agent-"):] if identity and identity.startswith("agent-AJ_") else None


class Link(Protocol):
    """What the recorder needs from LiveKit (`livekit_rtc.RtcLink`, or a fake)."""

    def room(self) -> Any: ...
    async def connect(self, room: Any, url: str, api_key: str, api_secret: str, room_name: str, identity: str) -> None: ...
    async def dispatches(self, url: str, api_key: str, api_secret: str, room_name: str) -> list[tuple[str, str]]: ...
    def is_agent(self, participant: Any) -> bool: ...
    def room_closed(self, reason: Any) -> bool: ...


async def watch_room(store: Store, ref: LiveRoomRef, on_closed: Callable[[SessionRef], Any], *, link: Link | None = None,
                     stop: asyncio.Event | None = None, clock: Callable[[], float] = time.monotonic, poll_s: float = POLL_S,
                     grace_s: float = GRACE_S, drain_s: float = DRAIN_S) -> dict:
    """Record one room until the call ends; `on_closed` gets the session once it closes. `stop` ends it early
    (the room's `room_finished` webhook)."""
    cap = Capture(store)
    ws = ref.workspace_id
    setting = await asyncio.to_thread(cap.setting, ws, PLATFORM)
    key = await asyncio.to_thread(cap.secret, ws, PLATFORM, LIVEKIT_KEY)
    secret = await asyncio.to_thread(cap.secret, ws, PLATFORM, LIVEKIT_SECRET)
    if setting is None or not setting.url or not key or not secret:
        return {"status": "not connected", "turns": 0}
    if link is None:
        try:
            from .livekit_rtc import RtcLink  # optional: pip install 'sopops-server[livekit]'
        except ImportError:
            log.warning("a LiveKit agent joined room %s in %s, but this sopops can't join rooms: pip install 'sopops-server[livekit]'", ref.room, ws)
            return {"status": "no livekit", "turns": 0}
        link = RtcLink()

    stop = stop or asyncio.Event()
    room = link.room()
    segments: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
    agents: set[str] = {ref.agent_identity} if ref.agent_identity else set()
    names: list[str] = [ref.agent_name] if ref.agent_name else []
    state: dict[str, Any] = {"over": False, "reason": "", "leaving": False, "gone": {"agent": None, "caller": None}}
    readers: set[asyncio.Task] = set()

    def side(p: Any) -> str:
        return "agent" if link.is_agent(p) or p.identity in agents else "caller"

    def note(p: Any) -> None:
        s = side(p)
        if s == "agent":
            agents.add(p.identity)
            if (n := agent_name(getattr(p, "attributes", None))) and n not in names:
                names.insert(0, n)
        state["gone"][s] = None

    def present(s: str) -> bool:
        return any(side(p) == s for p in room.remote_participants.values())

    async def read(reader: Any, identity: str) -> None:
        chunks, size = [], 0
        try:
            async for chunk in reader:
                size += len(chunk)
                if size > MAX_SEGMENT_CHARS:
                    log.warning("a transcript segment in room %s passed %d characters; dropped", ref.room, MAX_SEGMENT_CHARS)
                    return
                chunks.append(chunk)
        except Exception as e:  # an aborted stream is an interrupted segment
            log.debug("transcript stream from %s ended early: %s", identity, type(e).__name__)
            return
        text = "".join(chunks).strip()
        attributes = dict(getattr(getattr(reader, "info", None), "attributes", None) or {})
        if text and (identity in agents or attributes.get(FINAL) == "true"):
            await segments.put((attributes.get(SEGMENT_ID) or f"{identity}:{text}", identity, text))

    def on_stream(reader: Any, identity: str) -> None:
        if len(readers) >= MAX_OPEN_STREAMS:
            log.warning("%d transcript streams already open in room %s; refusing one", MAX_OPEN_STREAMS, ref.room)
            return
        task = asyncio.ensure_future(read(reader, identity))
        readers.add(task)
        task.add_done_callback(readers.discard)

    def on_segments(segs: Any, participant: Any = None, *_: Any) -> None:  # older agents: transcription events
        identity = getattr(participant, "identity", "") or ""
        for s in segs or []:
            if getattr(s, "final", False) and (getattr(s, "text", "") or "").strip():
                segments.put_nowait((getattr(s, "id", "") or f"{identity}:{s.text}", identity, s.text.strip()))

    def on_left(p: Any) -> None:
        s = side(p)
        if not present(s):
            state["gone"][s] = clock()

    def on_disconnected(reason: Any = None) -> None:
        if state["leaving"]:
            return
        state["over"] = link.room_closed(reason)
        state["reason"] = f"disconnected ({reason})"
        stop.set()

    room.register_text_stream_handler(TRANSCRIPTION_TOPIC, on_stream)
    room.on("transcription_received", on_segments)
    room.on("participant_connected", note)
    room.on("participant_attributes_changed", lambda _changed, p: note(p))
    room.on("participant_disconnected", on_left)
    room.on("disconnected", on_disconnected)

    identity = f"{RECORDER_PREFIX}-{os.urandom(6).hex()}"
    try:
        await link.connect(room, setting.url, key, secret, ref.room, identity)
    except Exception as e:
        log.warning("couldn't join LiveKit room %s for %s: %s", ref.room, ws, type(e).__name__)
        return {"status": "join failed", "turns": 0}

    external_id: str | None = None
    seq = 0
    written: set[str] = set()

    async def write(seg_id: str, sender: str, text: str) -> None:
        nonlocal seq
        if seg_id in written:
            return
        written.add(seg_id)
        event = SessionEventIn(seq=seq, type="turn", turn={"role": "agent" if sender in agents else "user", "text": text, "at": time.time()})
        seq += 1
        await asyncio.to_thread(store.sessions.append, ws, PLATFORM, external_id, [event])

    try:
        joined = clock()
        state["gone"] = {"agent": joined, "caller": joined}  # a side that isn't here when we arrive counts as gone from now
        for p in list(room.remote_participants.values()):
            note(p)
        metadata: list[str] = []
        try:
            for name, meta in await link.dispatches(setting.url, key, secret, ref.room):
                if name and name not in names:
                    names.append(name)
                metadata.append(meta)
        except Exception as e:  # the dispatch is a hint; the room still gets recorded
            log.info("couldn't list the agent dispatches of room %s: %s", ref.room, type(e).__name__)
        metadata.append(getattr(room, "metadata", "") or "")
        agent, release = await asyncio.to_thread(cap.livekit_agent, ws, names[0] if names else None, metadata)
        identities = [ref.agent_identity, *sorted(agents)]
        external_id = next((j for j in map(job_id, identities) if j), None) or ref.room_sid or ref.room
        meta = {k: v for k, v in {"room": ref.room, "room_sid": ref.room_sid, "agent_name": names[0] if names else None, "source": "livekit-recorder"}.items() if v}
        await asyncio.to_thread(store.sessions.open, ws, SessionOpen(platform=PLATFORM, external_id=external_id, agent=agent, release=release, metadata=meta))
        log.info("recording LiveKit room %s in %s as session %s", ref.room, ws, external_id)

        while not stop.is_set():
            try:
                seg = await asyncio.wait_for(segments.get(), timeout=poll_s)
            except TimeoutError:
                seg = None
            if seg is not None:
                await write(*seg)
            now = clock()
            gone = [s for s, since in state["gone"].items() if since is not None and now - since >= grace_s]
            if gone:
                state["over"], state["reason"] = True, f"every {gone[0]} left"
                break
            if now - joined >= MAX_CALL_S:
                state["reason"] = "the longest call sopops records"
                break
        else:
            if not state["reason"]:  # stopped from outside: the room finished
                state["over"], state["reason"] = True, "the room finished"
    except Exception:
        log.exception("recording LiveKit room %s failed", ref.room)
        state["reason"] = "error"
    finally:
        state["leaving"] = True
        try:
            room.unregister_text_stream_handler(TRANSCRIPTION_TOPIC)
        except Exception:
            pass
        try:
            await room.disconnect()
        except Exception:
            log.warning("couldn't leave LiveKit room %s cleanly", ref.room)

    if external_id is None:
        return {"status": state["reason"] or "not opened", "turns": 0}
    if readers:  # segments already being read are still this call's
        _, pending = await asyncio.wait(set(readers), timeout=drain_s)
        for t in pending:
            t.cancel()
    while not segments.empty():
        await write(*segments.get_nowait())
    if not state["over"]:
        log.warning("stopped recording room %s while it may still be live (%s); the session stays open", ref.room, state["reason"])
        return {"status": "left open", "turns": seq, "reason": state["reason"]}
    done = await asyncio.to_thread(store.sessions.close, ws, PLATFORM, external_id)
    if done.changed:
        await asyncio.to_thread(on_closed, done.ref)
    log.info("recorded LiveKit room %s: %d turns (%s)", ref.room, seq, state["reason"])
    return {"status": "closed", "turns": seq, "reason": state["reason"], "external_id": external_id}
