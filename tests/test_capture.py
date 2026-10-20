"""Sessions with no code in the agent (sopops_server.capture): LiveKit's signed webhooks and the room recorder (a fake room;
no LiveKit), ElevenLabs' post-call webhook and Enterprise monitor socket (a fake socket), and Vapi's server messages.
Nothing reaches a real platform."""

import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
import respx
from conftest import SOPC_BIN, WS, fresh_db_url
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_runtime import platform_files
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from sopops_server import Sopc, Store, create_app
from sopops_server.capture import Capture, LiveConversationRef, LiveRoomRef
from sopops_server.capture import elevenlabs as el
from sopops_server.capture import livekit as lk
from sopops_server.capture import vapi
from sopops_server.jobs import Jobs

LK_KEY, LK_SECRET = "APIdemo123", "lk-secret-0123456789abcdef0123456789"
POSTCALL = "wsec_postcall_0123456789"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("SOPOPS_LIVEKIT_ANY_HOST", raising=False)


class RecordingJobs(Jobs):
    """Jobs that note the recorders it's asked to start instead of joining anything."""

    def __init__(self, store):
        super().__init__(store, use_hatchet=False)
        self.rooms: list[LiveRoomRef] = []
        self.conversations: list[LiveConversationRef] = []
        self.ended: list[tuple[str, str]] = []

    def watch_livekit_room(self, ref):
        self.rooms.append(ref)
        return "inline"

    def end_livekit_room(self, workspace, room):
        self.ended.append((workspace, room))
        return False

    def watch_elevenlabs(self, ref):
        self.conversations.append(ref)
        return "inline"


@pytest.fixture
def app(tmp_path, files):
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    jobs = RecordingJobs(store)
    client = TestClient(create_app(store, jobs))
    res = client.post(f"{WS}/publish", json={"files": platform_files(files)})  # luigis on ElevenLabs, sakura on Vapi, tonys on LiveKit
    assert res.status_code == 200, res.text
    return client, store, jobs


def sessions(client, **q):
    return client.get(f"{WS}/sessions", params=q).json()["items"]


# --- LiveKit: settings and the signed webhook ---------------------------------------------------------


def lk_token(body: bytes, key=LK_KEY, secret=LK_SECRET, **claims) -> str:
    now = int(time.time())
    sha = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return jwt.encode({"iss": key, "nbf": now - 5, "exp": now + 300, "sha256": sha, **claims}, secret, algorithm="HS256")


def connect_livekit(client, **extra):
    return client.put(f"{WS}/capture/livekit", json={"url": "wss://demo-proj.livekit.cloud", "api_key": LK_KEY, "api_secret": LK_SECRET, **extra})


def test_livekit_settings_are_stored_encrypted_and_never_returned(app):
    client, store, _ = app
    assert client.get(f"{WS}/capture").json()["livekit"]["connected"] is False
    assert client.put(f"{WS}/capture/livekit", json={"url": "https://example.com"}).status_code == 400  # LiveKit Cloud only
    assert client.put(f"{WS}/capture/livekit", json={"url": "wss://demo-proj.livekit.cloud"}).status_code == 400  # no key yet
    res = connect_livekit(client, agent_map={"*": "tonys-pizza"})
    assert res.status_code == 200, res.text
    out = client.get(f"{WS}/capture").json()
    text = json.dumps(out)
    assert LK_SECRET not in text and LK_KEY not in text
    lkout = out["livekit"]
    assert lkout["connected"] and lkout["url"] == "https://demo-proj.livekit.cloud" and lkout["agent_map"] == {"*": "tonys-pizza"}
    assert lkout["webhook_path"] == "/v1/workspaces/demo/capture/livekit/webhook"
    assert out["vapi"]["server_messages"] == ["assistant-request", "end-of-call-report", "conversation-update", "transcript"]
    # updating the URL or mapping keeps the stored key and secret
    assert client.put(f"{WS}/capture/livekit", json={"url": "demo-proj.livekit.cloud"}).status_code == 400
    assert client.put(f"{WS}/capture/livekit", json={"url": "https://demo-proj.livekit.cloud/", "agent_map": {}}).json()["connected"]
    assert client.delete(f"{WS}/capture/livekit").status_code == 204
    assert client.get(f"{WS}/capture").json()["livekit"]["connected"] is False


def test_livekit_webhook_verification(app):
    client, _, jobs = app
    url = f"{WS}/capture/livekit/webhook"
    body = json.dumps({"event": "participant_joined", "room": {"name": "call-1", "sid": "RM_1"},
                       "participant": {"identity": "agent-AJ_123", "kind": "AGENT", "attributes": {"lk.agent_name": "tonys-pizza"}}}).encode()
    headers = {"Content-Type": "application/webhook+json"}
    assert client.post(url, content=body, headers={**headers, "Authorization": lk_token(body)}).status_code == 401  # nothing connected yet
    connect_livekit(client)
    for bad in (
        lk_token(body, secret="another-secret-0123456789abcdef0123"),  # signed with another secret
        lk_token(body, key="APIother"),  # issued by another key
        lk_token(body + b" "),  # for another body
        lk_token(body, exp=int(time.time()) - 600),  # expired
        "not-a-jwt",
    ):
        assert client.post(url, content=body, headers={**headers, "Authorization": bad}).status_code == 401
    assert client.post(url, content=body, headers=headers).status_code == 401
    assert jobs.rooms == []

    res = client.post(url, content=body, headers={**headers, "Authorization": f"Bearer {lk_token(body)}"})
    assert res.status_code == 200 and res.json()["status"] == "recording"
    assert jobs.rooms == [LiveRoomRef(workspace_id="demo", room="call-1", room_sid="RM_1", agent_identity="agent-AJ_123", agent_name="tonys-pizza")]
    assert client.get(f"{WS}/capture").json()["livekit"]["last_event_at"]

    caller = json.dumps({"event": "participant_joined", "room": {"name": "call-1"}, "participant": {"identity": "caller", "kind": "STANDARD"}}).encode()
    assert client.post(url, content=caller, headers={**headers, "Authorization": lk_token(caller)}).json()["status"] == "acknowledged"
    done = json.dumps({"event": "room_finished", "room": {"name": "call-1"}}).encode()
    assert client.post(url, content=done, headers={**headers, "Authorization": lk_token(done)}).json()["status"] == "finished"
    assert jobs.ended == [("demo", "call-1")] and len(jobs.rooms) == 1


def test_livekit_agent_mapping(app):
    client, store, _ = app
    cap = Capture(store)
    connect_livekit(client)
    assert cap.livekit_agent("demo", "tonys-pizza", []) == ("tonys-pizza", None)  # the dispatch's agent name
    # the name matches nothing: job or room metadata, with a release pin
    assert cap.livekit_agent("demo", "worker-7", ['{"sopops": {"agent": "sakura-sushi", "release": 1}}', ""]) == ("sakura-sushi", 1)
    assert cap.livekit_agent("demo", None, [{"sopops_release": "2"}, '{"sopops": {"agent": "luigis-trattoria"}}']) == ("luigis-trattoria", 2)
    # then the workspace's mapping, by name or any
    connect_livekit(client, agent_map={"worker-7": "tonys-pizza", "*": "sakura-sushi"})
    assert cap.livekit_agent("demo", "worker-7", ["not json"]) == ("tonys-pizza", None)
    assert cap.livekit_agent("demo", "worker-9", []) == ("sakura-sushi", None)
    connect_livekit(client, agent_map={})
    assert cap.livekit_agent("demo", "worker-9", []) == ("worker-9", None)  # kept as sent


# --- LiveKit: the room recorder, with a fake room ----------------------------------------------------


class Participant:
    def __init__(self, identity, kind="standard", attributes=None):
        self.identity, self.kind, self.attributes = identity, kind, attributes or {}


class Info:
    def __init__(self, attributes):
        self.attributes = attributes


class Reader:
    def __init__(self, chunks, attributes):
        self.chunks, self.info = chunks, Info(attributes)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self.chunks:
            await asyncio.sleep(0)
            yield c


class FakeRoom:
    def __init__(self, participants, metadata=""):
        self.remote_participants = {p.identity: p for p in participants}
        self.metadata = metadata
        self.handlers, self.stream, self.left = {}, None, False

    def on(self, event, cb):
        self.handlers[event] = cb

    def register_text_stream_handler(self, topic, handler):
        assert topic == "lk.transcription"
        self.stream = handler

    def unregister_text_stream_handler(self, topic):
        self.stream = None

    async def disconnect(self):
        self.left = True

    # the test's side
    def say(self, identity, text, **attrs):
        self.stream(Reader([text[: len(text) // 2], text[len(text) // 2:]], attrs), identity)

    def leave(self, identity):
        p = self.remote_participants.pop(identity)
        self.handlers["participant_disconnected"](p)


class FakeLink:
    def __init__(self, room, dispatches=()):
        self._room, self._dispatches, self.joined = room, list(dispatches), None

    def room(self):
        return self._room

    async def connect(self, room, url, api_key, api_secret, room_name, identity):
        assert (url, api_key, api_secret) == ("https://demo-proj.livekit.cloud", LK_KEY, LK_SECRET)
        self.joined = (room_name, identity)

    async def dispatches(self, url, api_key, api_secret, room_name):
        return self._dispatches

    def is_agent(self, p):
        return p.kind == "agent"

    def room_closed(self, reason):
        return reason == "deleted"


def record(store, room, ref, script, link=None, **kw):
    closed = []
    link = link or FakeLink(room, [("tonys-pizza", "")])

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(lk.watch_room(store, ref, closed.append, link=link, stop=stop, poll_s=0.01, grace_s=0.2, drain_s=1, **kw))
        while link.joined is None:
            await asyncio.sleep(0.01)
        await script(room, stop)
        return await asyncio.wait_for(task, 10)

    return asyncio.run(main()), closed, link


def test_a_room_streams_into_a_session_that_closes_when_the_call_ends(app):
    client, store, _ = app
    connect_livekit(client)
    agent = Participant("agent-AJ_123", "agent", {"lk.agent_name": "tonys-pizza"})
    room = FakeRoom([agent, Participant("caller-1")])

    async def script(room, stop):
        room.say("caller-1", "Hi, a large pepperoni", **{"lk.segment_id": "s1", "lk.transcription_final": "false"})  # interim: dropped
        await asyncio.sleep(0.05)
        room.say("caller-1", "Hi, a large pepperoni please", **{"lk.segment_id": "s1", "lk.transcription_final": "true"})
        await asyncio.sleep(0.05)
        live = sessions(client, platform="livekit")
        assert [s["status"] for s in live] == ["open"]  # streaming while the call runs
        room.say("agent-AJ_123", "Sure, one large pepperoni.", **{"lk.segment_id": "s2"})  # an agent stream needn't say final
        await asyncio.sleep(0.05)
        room.say("caller-1", "Hi, a large pepperoni please", **{"lk.segment_id": "s1", "lk.transcription_final": "true"})  # again: once
        await asyncio.sleep(0.05)
        room.leave("caller-1")

    out, closed, link = record(store, room, LiveRoomRef(workspace_id="demo", room="call-1", room_sid="RM_1", agent_identity="agent-AJ_123"), script)
    assert out["status"] == "closed" and out["external_id"] == "AJ_123" and out["turns"] == 2  # keyed by job id, as attach() keys it
    assert link.joined[0] == "call-1" and link.joined[1].startswith("sopops-recorder-") and room.left
    [s] = sessions(client, platform="livekit")
    detail = client.get(f"{WS}/sessions/{s['id']}").json()
    assert s["external_id"] == "AJ_123" and s["agent_id"] == "tonys-pizza" and s["status"] != "open" and s["ended_at"]
    assert [(t["role"], t["text"]) for t in detail["turns"]] == [("user", "Hi, a large pepperoni please"), ("agent", "Sure, one large pepperoni.")]
    assert detail["metadata"]["room"] == "call-1" and detail["metadata"]["source"] == "livekit-recorder"
    assert [r.session_id for r in closed] == [s["id"]]  # handed to the pipeline once


def test_a_room_that_finishes_or_drops(app):
    client, store, _ = app
    connect_livekit(client)

    async def finished(room, stop):  # room_finished webhook
        room.say("caller-1", "Hello?", **{"lk.transcription_final": "true"})
        await asyncio.sleep(0.05)
        stop.set()

    room = FakeRoom([Participant("bot", "agent"), Participant("caller-1")], metadata='{"sopops": {"agent": "sakura-sushi"}}')
    out, closed, _ = record(store, room, LiveRoomRef(workspace_id="demo", room="call-2", room_sid="RM_2"), finished, link=FakeLink(room))
    assert out["status"] == "closed" and out["external_id"] == "RM_2" and len(closed) == 1  # no job id: the room's sid
    assert sessions(client, platform="livekit")[0]["agent_id"] == "sakura-sushi"  # from room metadata

    async def dropped(room, stop):  # our connection drops: the call may go on, so the session stays open
        room.handlers["disconnected"]("network")

    room = FakeRoom([Participant("agent-AJ_9", "agent"), Participant("caller-2")])
    out, closed, _ = record(store, room, LiveRoomRef(workspace_id="demo", room="call-3"), dropped)
    assert out["status"] == "left open" and closed == []
    assert {s["external_id"]: s["status"] for s in sessions(client, platform="livekit")}["AJ_9"] == "open"

    async def deleted(room, stop):
        room.handlers["disconnected"]("deleted")

    room = FakeRoom([Participant("agent-AJ_10", "agent")])
    out, closed, _ = record(store, room, LiveRoomRef(workspace_id="demo", room="call-4"), deleted)
    assert out["status"] == "closed" and len(closed) == 1


def test_no_recorder_without_a_connected_project(app):
    _, store, _ = app
    out = asyncio.run(lk.watch_room(store, LiveRoomRef(workspace_id="demo", room="r"), lambda r: None, link=FakeLink(FakeRoom([]))))
    assert out["status"] == "not connected"


# --- ElevenLabs post-call webhook ----------------------------------------------------------------------


def el_signed(raw: bytes, secret=POSTCALL, t=None) -> str:
    t = int(time.time()) if t is None else t
    return f"t={t},v0={hmac.new(secret.encode(), f'{t}.'.encode() + raw, hashlib.sha256).hexdigest()}"


def post_call_body(conversation_id="conv_1", **data):
    return {
        "type": "post_call_transcription",
        "event_timestamp": int(time.time()),
        "data": {
            "agent_id": "agent_luigi",
            "conversation_id": conversation_id,
            "status": "done",
            "transcript": [
                {"role": "agent", "message": "Luigi's, how can I help?", "time_in_call_secs": 0},
                {"role": "user", "message": "Do you have a table for two at eight?", "time_in_call_secs": 3},
                {"role": "agent", "message": None, "time_in_call_secs": 5,
                 "tool_calls": [{"request_id": "r1", "tool_name": "check_tables", "params_as_json": '{"party": 2, "time": "20:00"}', "tool_has_been_called": True}],
                 "tool_results": [{"request_id": "r1", "tool_name": "check_tables", "result_value": '{"free": true}', "is_error": False}]},
                {"role": "agent", "message": "Yes, we do. Under what name?", "time_in_call_secs": 7},
            ],
            "metadata": {"start_time_unix_secs": 1760800000, "call_duration_secs": 42, "termination_reason": "end_call tool", "phone_call": {"call_sid": "CA1"}},
            "analysis": {"call_successful": "success"},
            "conversation_initiation_client_data": {"dynamic_variables": {"system__agent_id": "agent_luigi"}},
            **data,
        },
    }


def test_elevenlabs_signature():
    raw = b'{"a":1}'
    now = time.time()
    assert el.verify_signature(el_signed(raw), raw, POSTCALL)
    assert not el.verify_signature(el_signed(raw, secret="other"), raw, POSTCALL)
    assert not el.verify_signature(el_signed(raw), raw + b" ", POSTCALL)
    assert not el.verify_signature(el_signed(raw, t=int(now) - 3600), raw, POSTCALL)  # too old
    assert not el.verify_signature(el_signed(raw, t=int(now) + 3600), raw, POSTCALL)  # from the future
    for bad in (None, "", "t=abc,v0=1", "v0=deadbeef"):
        assert not el.verify_signature(bad, raw, POSTCALL)


def test_elevenlabs_post_call_conversion():
    s = el.post_call(post_call_body())
    assert s.platform == "elevenlabs" and s.external_id == "conv_1" and s.agent == "agent_luigi" and not s.partial
    assert [(t.role, t.text) for t in s.turns] == [
        ("agent", "Luigi's, how can I help?"), ("user", "Do you have a table for two at eight?"),
        ("tool", 'check_tables({"party": 2, "time": "20:00"})'), ("agent", "Yes, we do. Under what name?"),
    ]
    [call] = s.tool_calls
    assert (call.name, call.arguments, call.result, call.is_error, call.call_id) == ("check_tables", {"party": 2, "time": "20:00"}, {"free": True}, False, "r1")
    assert s.ended_at.timestamp() - s.started_at.timestamp() == 42 and s.metadata["phone_call"] == {"call_sid": "CA1"}
    pinned = el.post_call(post_call_body(conversation_initiation_client_data={"dynamic_variables": {"sopops_release": "3", "sopops_agent": "luigis-trattoria"}}))
    assert (pinned.release, pinned.agent) == (3, "luigis-trattoria")
    assert el.post_call(post_call_body(status="failed")).partial
    assert el.post_call({"type": "post_call_audio", "data": {"conversation_id": "c"}}) is None


def test_elevenlabs_post_call_webhook(app):
    client, _, _ = app
    url = f"{WS}/capture/elevenlabs/post-call"
    raw = json.dumps(post_call_body()).encode()
    assert client.post(url, content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).status_code == 401  # no secret stored
    assert client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL}).status_code == 200
    assert POSTCALL not in json.dumps(client.get(f"{WS}/capture").json())
    assert client.post(url, content=raw, headers={"ElevenLabs-Signature": el_signed(raw, secret="wrong")}).status_code == 401
    assert client.post(url, content=raw).status_code == 401
    assert sessions(client) == []

    res = client.post(url, content=raw, headers={"ElevenLabs-Signature": el_signed(raw)})
    assert res.status_code == 200 and res.json()["status"] == "created"
    [s] = sessions(client)
    assert (s["platform"], s["external_id"], s["agent_id"], s["tool_call_count"]) == ("elevenlabs", "conv_1", "luigis-trattoria", 1)  # mapped by platform id
    again = client.post(url, content=raw, headers={"ElevenLabs-Signature": el_signed(raw)})
    assert again.json() == {"status": "unchanged", "session_id": s["id"]}  # a redelivery changes nothing
    audio = json.dumps({"type": "post_call_audio", "data": {"conversation_id": "conv_1"}}).encode()
    assert client.post(url, content=audio, headers={"ElevenLabs-Signature": el_signed(audio)}).json()["status"] == "ignored"
    assert client.get(f"{WS}/capture").json()["elevenlabs"]["last_post_call_at"]


# --- ElevenLabs Enterprise: the monitor socket ----------------------------------------------------------


class Sock:
    def __init__(self, frames, error=None, refuse=None):
        self.frames, self.error, self.refuse = frames, error, refuse

    async def __aenter__(self):
        if self.refuse:
            raise InvalidStatus(Response(self.refuse, "Refused", Headers()))
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield json.dumps(f)
        if self.error:
            raise self.error


def connector(*socks):
    calls = []

    def connect(url, additional_headers=None, open_timeout=None):
        calls.append((url, additional_headers))
        return socks[min(len(calls), len(socks)) - 1]

    connect.calls = calls
    return connect


async def no_sleep(_):
    return None


def el_key(client):
    assert client.put(f"{WS}/runtime/platforms/elevenlabs/api-key", json={"value": "el-test-key-123456"}).status_code == 200


def test_conversation_init_starts_live_monitoring_when_on(app):
    client, _, jobs = app
    url = f"{WS}/runtime/elevenlabs/conversation-init"
    body = {"agent_id": "agent_luigi", "conversation_id": "conv_9", "call_sid": "CA9"}
    assert client.post(url, json=body).status_code == 200 and jobs.conversations == []  # off by default
    assert client.put(f"{WS}/capture/elevenlabs/live", json={"on": True}).json()["live"] is True
    assert client.post(url, json=body).status_code == 200
    [ref] = jobs.conversations
    assert (ref.conversation_id, ref.agent, ref.release) == ("conv_9", "luigis-trattoria", 1)


def test_live_monitoring_streams_and_the_post_call_webhook_wins(app):
    client, store, _ = app
    el_key(client)
    client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    frames = [
        {"type": "conversation_initiation_metadata", "conversation_initiation_metadata_event": {}},
        {"type": "agent_response", "agent_response_event": {"agent_response": "Luigi's, how can I help?", "event_id": 1}},
        {"type": "user_transcript", "user_transcription_event": {"user_transcript": "Do you have a table for two at eight?", "event_id": 2}},
        {"type": "ping", "ping_event": {"event_id": 3}},
    ]
    drop = ConnectionClosedError(Close(1011, "blip"), None)
    # the conversation hasn't started (404), then a drop mid-call, then a reconnect that replays what was seen
    connect = connector(Sock([], refuse=404), Sock(frames[:2], error=drop), Sock(frames))
    ref = LiveConversationRef(workspace_id="demo", conversation_id="conv_1", agent="luigis-trattoria", release=1)
    closed = []
    out = asyncio.run(el.watch_conversation(store, ref, closed.append, connect=connect, sleep=no_sleep, grace_s=0))
    assert out == {"status": "done", "turns": 2}
    assert connect.calls[0] == ("wss://api.elevenlabs.io/v1/convai/conversations/conv_1/monitor", {"xi-api-key": "el-test-key-123456"})
    [s] = sessions(client)
    detail = client.get(f"{WS}/sessions/{s['id']}").json()
    assert [t["text"] for t in detail["turns"]] == ["Luigi's, how can I help?", "Do you have a table for two at eight?"]  # replays deduplicated
    assert s["status"] != "open" and len(closed) == 1
    assert client.get(f"{WS}/capture").json()["elevenlabs"]["live_state"] == "on"

    raw = json.dumps(post_call_body()).encode()
    res = client.post(f"{WS}/capture/elevenlabs/post-call", content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).json()
    assert res == {"status": "updated", "session_id": s["id"]}  # the same session, now whole
    [s2] = sessions(client)
    assert s2["id"] == s["id"] and s2["turn_count"] == 4 and s2["tool_call_count"] == 1


def test_enterprise_monitoring_falls_back_quietly(app):
    client, store, _ = app
    ref = LiveConversationRef(workspace_id="demo", conversation_id="conv_2", agent="luigis-trattoria")
    out = asyncio.run(el.watch_conversation(store, ref, lambda r: None, connect=connector(Sock([])), sleep=no_sleep))
    assert out["status"] == "unavailable"  # no API key
    el_key(client)
    for sock, words in (
        (Sock([], refuse=403), "Enterprise"),  # not an Enterprise plan, or the key lacks the scope
        (Sock([], error=ConnectionClosedError(Close(1008, "monitoring disabled"), None)), "Monitoring"),  # off for the agent
    ):
        out = asyncio.run(el.watch_conversation(store, ref, lambda r: None, connect=connector(sock), sleep=no_sleep, grace_s=0))
        assert out["status"] == "unavailable"
        state = client.get(f"{WS}/capture").json()["elevenlabs"]
        assert state["live_state"] == "unavailable" and words in state["live_detail"] and state["live_checked_at"]
    assert sessions(client) == []  # a refused monitor leaves nothing behind
    # the post-call webhook still brings the session
    client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    raw = json.dumps(post_call_body("conv_2")).encode()
    assert client.post(f"{WS}/capture/elevenlabs/post-call", content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).json()["status"] in ("created", "updated")
    assert client.put(f"{WS}/capture/elevenlabs/live", json={"on": False}).json()["live_state"] is None


def test_monitor_frames():
    assert el.parse_frame("not json").kind == "other" and el.parse_frame("[1]").kind == "other"
    f = el.parse_frame(json.dumps({"type": "user_transcript", "user_transcription_event": "oops"}))
    assert f.kind == "turn" and f.text == ""  # malformed: no text, so it's skipped
    t = el.parse_frame(json.dumps({"type": "agent_tool_response", "agent_tool_response": {"tool_name": "book", "tool_call_id": "t1", "is_error": False}}))
    assert t.kind == "tool" and t.tool == {"name": "book", "call_id": "t1", "is_error": False}


# --- Vapi: end-of-call report and live messages -----------------------------------------------------------


def vapi_messages():
    return [
        {"role": "system", "message": "You are Sakura's assistant.", "time": 1, "secondsFromStart": 0},
        {"role": "bot", "message": "Sakura Sushi, how can I help?", "time": 2, "secondsFromStart": 0.5},
        {"role": "user", "message": "Is the omakase available tonight?", "time": 3, "secondsFromStart": 4.2},
        {"role": "tool_calls", "toolCalls": [{"id": "tc1", "type": "function", "function": {"name": "check_omakase", "arguments": '{"date": "today"}'}}], "secondsFromStart": 5},
        {"role": "tool_call_result", "toolCallId": "tc1", "name": "check_omakase", "result": "2 seats left", "secondsFromStart": 5.5},
        {"role": "bot", "message": "Yes, two seats are left.", "time": 4, "secondsFromStart": 6},
    ]


def test_vapi_conversion():
    msg = {"type": "end-of-call-report", "endedReason": "customer-ended-call", "startedAt": "2026-10-18T10:00:00Z", "endedAt": "2026-10-18T10:01:00Z",
           "call": {"id": "call_1", "assistantId": "asst_sakura", "type": "inboundPhoneCall"}, "artifact": {"messages": vapi_messages()}}
    s = vapi.end_of_call(msg)
    assert (s.platform, s.external_id, s.agent, s.channel) == ("vapi", "call_1", "asst_sakura", "voice")
    assert [(t.role, t.text) for t in s.turns] == [
        ("bot", "Sakura Sushi, how can I help?"), ("user", "Is the omakase available tonight?"),
        ("tool", 'check_omakase({"date": "today"})'), ("bot", "Yes, two seats are left."),
    ]
    assert [(c.name, c.result, c.call_id) for c in s.tool_calls] == [("check_omakase", "2 seats left", "tc1")]
    assert s.metadata == {"ended_reason": "customer-ended-call", "call_type": "inboundPhoneCall"}
    tagged = {**msg, "call": {**msg["call"], "assistantOverrides": {"variableValues": {"sopops_agent": "sakura-sushi", "sopops_release": 2}}}}
    assert (vapi.end_of_call(tagged).agent, vapi.end_of_call(tagged).release) == ("sakura-sushi", 2)
    assert vapi.end_of_call({"type": "end-of-call-report"}) is None
    assert vapi.transcript_turn({"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "Is the"}) is None
    assert vapi.transcript_turn({"type": 'transcript[transcriptType="final"]', "role": "user", "transcript": "Hi"})["text"] == "Hi"


def test_vapi_server_url_streams_and_completes_sessions(app):
    client, _, _ = app
    url = f"{WS}/runtime/vapi/assistant-request"
    call = {"id": "call_7", "assistantId": "asst_sakura"}
    msgs = vapi_messages()

    def post(message, **headers):
        return client.post(url, json={"message": message}, headers=headers)

    assert post({"type": "transcript", "transcriptType": "final", "role": "assistant", "transcript": "Sakura Sushi, how can I help?", "call": call}).json() == {}
    assert post({"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "Is the", "call": call}).status_code == 200
    [s] = sessions(client)
    assert (s["status"], s["turn_count"], s["agent_id"]) == ("open", 1, "sakura-sushi")
    post({"type": "conversation-update", "messages": msgs[:3], "call": call})
    post({"type": "conversation-update", "messages": msgs, "call": call})  # the whole conversation each time: replaces
    post({"type": "conversation-update", "messages": msgs, "call": call})
    detail = client.get(f"{WS}/sessions/{s['id']}").json()
    assert [t["text"] for t in detail["turns"]][:2] == ["Sakura Sushi, how can I help?", "Is the omakase available tonight?"]
    assert detail["status"] == "open" and len(detail["turns"]) == 4 and len(detail["tool_calls"]) == 1

    report = {"type": "end-of-call-report", "endedReason": "customer-ended-call", "call": call, "artifact": {"messages": msgs}}
    assert post(report).json() == {}
    [done] = sessions(client)
    assert done["id"] == s["id"] and done["status"] != "open"
    post({"type": "conversation-update", "messages": msgs[:2], "call": call})  # late live message: the report won
    assert sessions(client)[0]["turn_count"] == 4

    client.put(f"{WS}/runtime/platforms/vapi/webhook-secret", json={"value": "vapi-hook"})
    report2 = {**report, "call": {"id": "call_8", "assistantId": "asst_sakura"}}
    assert post(report2).status_code == 401  # same auth as the prompt
    assert post(report2, **{"X-Vapi-Secret": "vapi-hook"}).status_code == 200
    assert len(sessions(client)) == 2


# --- what each call ran with (sopops_server.configs) -----------------------------------------------------------


def test_a_room_keeps_the_agents_sopops_attributes_as_its_config(app):
    client, store, _ = app
    connect_livekit(client)
    agent = Participant("agent-AJ_9", "agent", {"lk.agent_name": "tonys-pizza", "sopops_test": "haiku-vs-gpt", "sopops_llm": "openai/gpt-4.1-mini"})
    room = FakeRoom([agent, Participant("caller-1")], metadata=json.dumps({"sopops_release": 1, "sopops_version": "v7"}))

    async def script(room, stop):
        room.say("caller-1", "Hi", **{"lk.segment_id": "s1", "lk.transcription_final": "true"})
        await asyncio.sleep(0.05)
        agent.attributes["sopops_tts"] = "cartesia/sonic-2"  # set after the recorder joined
        room.handlers["participant_attributes_changed"]({"sopops_tts": "cartesia/sonic-2"}, agent)
        await asyncio.sleep(0.05)
        room.leave("caller-1")

    out, _, _ = record(store, room, LiveRoomRef(workspace_id="demo", room="call-9", room_sid="RM_9", agent_identity="agent-AJ_9"), script)
    assert out["status"] == "closed"
    [s] = sessions(client, platform="livekit")
    assert s["test"] == "haiku-vs-gpt" and s["release_id"] == 1
    assert s["config"] == {"llm": "openai/gpt-4.1-mini", "tts": "cartesia/sonic-2", "version": "v7"} and s["config_hash"]


@respx.mock
def test_elevenlabs_sessions_carry_the_agents_models(app):
    client, _, _ = app
    client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    client.put(f"{WS}/runtime/platforms/elevenlabs/api-key", json={"value": "el-test-key-123456"})
    route = respx.get("https://api.elevenlabs.io/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json={"conversation_config": {
        "agent": {"prompt": {"prompt": "You are Luigi's host.", "llm": "gemini-2.0-flash", "temperature": 0.3}},
        "tts": {"model_id": "eleven_flash_v2_5", "voice_id": "voice_rachel"}, "asr": {"provider": "elevenlabs"}}}))
    url = f"{WS}/capture/elevenlabs/post-call"
    for conv, extra in (("conv_a", {}), ("conv_b", {"sopops_test": "voices", "sopops_version": "b"})):
        body = post_call_body(conv, conversation_initiation_client_data={
            "dynamic_variables": {"sopops_agent": "luigis-trattoria", **extra},
            "conversation_config_override": {"tts": {"voice_id": "voice_adam"}} if extra else {}})
        raw = json.dumps(body).encode()
        assert client.post(url, content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).status_code == 200
    a, b = sorted(sessions(client), key=lambda s: s["external_id"])
    assert a["config"] == {"llm": "gemini-2.0-flash", "temperature": 0.3, "tts": "elevenlabs/eleven_flash_v2_5", "voice": "voice_rachel", "stt": "elevenlabs"}
    assert b["config"] == {**a["config"], "voice": "voice_adam", "version": "b"} and b["test"] == "voices"
    assert a["config_hash"] != b["config_hash"] and route.call_count == 1  # the agent is read once, then kept
    assert "el-test-key" not in json.dumps(sessions(client))


def test_vapi_sessions_carry_the_assistants_models(app):
    client, _, _ = app
    assistant = {"id": "asst_sakura", "model": {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0.5},
                 "voice": {"provider": "11labs", "voiceId": "burt", "model": "eleven_turbo_v2_5"}, "transcriber": {"provider": "deepgram", "model": "nova-3"}}
    call = {"id": "call_cfg", "assistantId": "asst_sakura", "assistantOverrides": {"model": {"model": "gpt-4.1"}, "metadata": {"sopops_test": "bigger-model"}}}
    report = {"type": "end-of-call-report", "call": call, "assistant": assistant, "artifact": {"messages": vapi_messages()}}
    assert client.post(f"{WS}/runtime/vapi/assistant-request", json={"message": report}).status_code == 200
    [s] = sessions(client)
    assert s["test"] == "bigger-model"
    assert s["config"] == {"llm": "openai/gpt-4.1", "temperature": 0.5, "tts": "11labs/eleven_turbo_v2_5", "voice": "burt", "stt": "deepgram/nova-3"}
