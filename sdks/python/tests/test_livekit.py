"""The LiveKit adapter, with stand-ins for JobContext and AgentSession (livekit isn't installed for these tests)."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import respx

from sopops_client import Sopops
from sopops_client.livekit import attach, from_livekit

URL = "http://sopops.test/v1/workspaces/prod"
AGENT = {"agent": "tonys-pizza", "platform_ref": "livekit:tonys-pizza", "release": 3, "hash": "abc", "prompt": "You are Tony's.", "tools": [], "tool_sops": [], "components": []}

HISTORY = {
    "items": [
        {"type": "message", "role": "assistant", "content": ["Tony's Pizza, how can I help?"], "created_at": 100.0},
        {"type": "message", "role": "user", "content": ["Delivery please."], "created_at": 104.0},
        {"type": "message", "role": "user", "content": ["Delivery please."], "created_at": 104.1},
        {"type": "function_call", "call_id": "c1", "name": "check_zone", "arguments": '{"zip": "10001"}', "created_at": 106.0},
        {"type": "function_call_output", "call_id": "c1", "name": "check_zone", "output": '{"ok": true}', "is_error": False, "created_at": 106.5},
        {"type": "message", "role": "assistant", "content": ["You're in the zone.", {"audio": "..."}], "interrupted": True, "created_at": 108.0},
    ]
}


class History:
    def to_dict(self, exclude_timestamp=True):
        assert exclude_timestamp is False  # timestamps on
        return HISTORY


class Session:
    """Like AgentSession: reading userdata before it's set raises ValueError."""

    def __init__(self, userdata=None):
        self._userdata = userdata
        self.history = History()

    @property
    def userdata(self):
        if self._userdata is None:
            raise ValueError("VoiceSession userdata is not set")
        return self._userdata

    @userdata.setter
    def userdata(self, value):
        self._userdata = value


def ctx(job_metadata="", room_metadata=""):
    callbacks = []
    return SimpleNamespace(
        job=SimpleNamespace(id="AJ_1", agent_name="tonys-pizza", metadata=job_metadata),
        room=SimpleNamespace(name="room-1", metadata=room_metadata),
        add_shutdown_callback=callbacks.append,
        callbacks=callbacks,
    )


def test_from_livekit_maps_turns_and_tools():
    turns, tools = from_livekit(HISTORY)
    assert [t["role"] for t in turns] == ["assistant", "user", "tool", "assistant"]  # the repeat is dropped
    assert turns[1] == {"role": "user", "text": "Delivery please.", "at": 104.0, "interrupted": False}
    assert turns[2]["name"] == "check_zone" and turns[3]["interrupted"] and turns[3]["text"] == "You're in the zone."
    assert tools == [{"name": "check_zone", "arguments": '{"zip": "10001"}', "call_id": "c1", "at": 106.0, "result": '{"ok": true}', "is_error": False}]
    assert from_livekit(History()) == (turns, tools)


@respx.mock
def test_attach_fetches_remembers_the_release_and_posts_at_shutdown(tmp_path):
    get = respx.get(f"{URL}/agents/livekit:tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT))
    post = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1"}))
    c, session = ctx(), Session()

    async def call():
        ins = await attach(c, session, client=Sopops(URL, "tok", cache_dir=tmp_path))
        assert ins.text == "You are Tony's." and session.userdata == {"sopops_release": 3, "sopops_agent": "tonys-pizza"}
        assert len(c.callbacks) == 1
        await c.callbacks[0]("user_left")

    asyncio.run(call())
    assert "release" not in get.calls.last.request.url.params
    body = json.loads(post.calls.last.request.content)
    assert body["platform"] == "livekit" and body["external_id"] == "AJ_1" and body["agent"] == "tonys-pizza" and body["release"] == 3
    assert body["metadata"] == {"room": "room-1", "shutdown_reason": "user_left"}
    assert len(body["turns"]) == 4 and body["tool_calls"][0]["result"] == '{"ok": true}'
    assert post.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
def test_a_release_pinned_in_metadata_is_fetched(tmp_path):
    get = respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json={**AGENT, "release": 2}))
    client = Sopops(URL, cache_dir=tmp_path)
    session = Session(userdata=SimpleNamespace(sopops_release=None, sopops_agent=None, order=[]))
    asyncio.run(attach(ctx(job_metadata='{"sopops_release": 2}'), session, "tonys-pizza", client=client))
    assert get.calls.last.request.url.params["release"] == "2" and session.userdata.sopops_release == 2

    asyncio.run(attach(ctx(room_metadata='{"sopops": {"release": 1}}'), Session(userdata={}), "tonys-pizza", client=client))
    assert get.calls.last.request.url.params["release"] == "1"


@respx.mock
def test_a_failed_post_never_breaks_shutdown(tmp_path):
    respx.get(f"{URL}/agents/tonys-pizza").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{URL}/sessions").mock(side_effect=httpx.ConnectError("down"))
    c = ctx()

    async def call():
        ins = await attach(c, Session(), "tonys-pizza", client=Sopops(URL, cache_dir=tmp_path), fallback="Be brief.")
        assert ins.source == "fallback"
        await c.callbacks[0]("done")  # logs, doesn't raise

    asyncio.run(call())
