"""The ElevenLabs adapter (the elevenlabs package isn't installed for these tests)."""

import asyncio
import json
import threading

import httpx
import pytest
import respx

from sopops import MemoryCache, SopOps
from sopops.elevenlabs import elevenlabs_session

URL = "http://sopops.test/v1/workspaces/prod"
AGENT = {"agent": "sakura-sushi", "platform_ref": "elevenlabs:agent_123", "release": 3, "hash": "abc", "prompt": "You are Sakura's host.",
         "tools": [], "tool_sops": [], "components": []}


def client():
    return SopOps(URL, "tok", cache=MemoryCache())


@respx.mock
def test_builds_the_overrides_and_links_the_release():
    get = respx.get(f"{URL}/agents/agent_123?platform=elevenlabs").mock(return_value=httpx.Response(200, json=AGENT))
    served = respx.post(f"{URL}/runtime/served").mock(return_value=httpx.Response(200, json={}))

    async def call():
        s = await elevenlabs_session(client(), "agent_123", test="haiku-vs-gpt", config={"version": "v2"}, tags=["legacy", "pilot"])
        assert s.agent_id == "agent_123" and s.instructions.text == "You are Sakura's host."
        assert s.overrides == {"agent": {"prompt": {"prompt": "You are Sakura's host."}}}
        assert s.dynamic_variables == {"sopops_agent": "sakura-sushi", "sopops_release": 3, "sopops_test": "haiku-vs-gpt", "sopops_version": "v2",
                                       "sopops_tags": "legacy,pilot"}
        assert s.initiation_data == {"type": "conversation_initiation_client_data", "conversation_config_override": s.overrides,
                                     "dynamic_variables": s.dynamic_variables}
        assert s.callbacks == {}  # no stream: nothing to keep
        await s.end(conversation_id="conv_9")  # links, posts nothing without stream

    asyncio.run(call())
    assert get.called and json.loads(served.calls.last.request.content) == {"platform": "elevenlabs", "call_id": "conv_9", "agent": "sakura-sushi", "release": 3}


@respx.mock
def test_refuses_an_agent_that_isnt_on_elevenlabs():
    respx.get(f"{URL}/agents/front-desk?platform=elevenlabs").mock(return_value=httpx.Response(200, json={**AGENT, "platform_ref": "livekit:front-desk"}))
    with pytest.raises(ValueError, match="isn't an ElevenLabs agent"):
        asyncio.run(elevenlabs_session(client(), "front-desk"))


@respx.mock
def test_linking_never_raises():
    respx.get(f"{URL}/agents/agent_123?platform=elevenlabs").mock(return_value=httpx.Response(200, json=AGENT))
    respx.post(f"{URL}/runtime/served").mock(side_effect=httpx.ConnectError("down"))

    async def call():
        s = await elevenlabs_session(client(), "agent_123")
        await s.link("conv_1")

    asyncio.run(call())


@respx.mock
def test_stream_keeps_turns_from_another_thread_then_posts_the_conversation():
    respx.get(f"{URL}/agents/agent_123?platform=elevenlabs").mock(return_value=httpx.Response(200, json=AGENT))
    served = respx.post(f"{URL}/runtime/served").mock(return_value=httpx.Response(200, json={}))
    opened = respx.post(f"{URL}/sessions/open").mock(return_value=httpx.Response(201, json={"id": "s1", "created": True}))
    events = respx.post(f"{URL}/sessions/elevenlabs/conv_1/events").mock(return_value=httpx.Response(200, json={"accepted": 1, "duplicates": 0}))
    post = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1"}))
    guidance = []

    async def call():
        s = await elevenlabs_session(client(), "agent_123", stream=True, test="t1", on_guidance=guidance.append)
        cb = s.callbacks
        cb["callback_agent_response"]("Sakura Sushi, how can I help?")  # before the conversation id is known
        s.connect("conv_1")
        # the ElevenLabs SDK calls back from its own thread
        t = threading.Thread(target=lambda: (cb["callback_user_transcript"]("A table for four?"),
                                             s.tool_result("check_reservations", "t1", '{"available": true}')))
        t.start()
        await asyncio.to_thread(t.join)
        await asyncio.sleep(0.7)  # one flush interval
        await s.end("user")
        await s.end("again")  # the same post

    asyncio.run(call())
    assert served.call_count == 1 and opened.call_count == 1 and post.call_count == 1
    assert json.loads(opened.calls.last.request.content)["test"] == "t1"
    sent = [e for c in events.calls for e in json.loads(c.request.content)["events"]]
    assert [(e["seq"], e["type"]) for e in sent] == [(0, "turn"), (1, "turn"), (2, "turn"), (3, "tool_call")]
    assert sent[3]["tool_call"]["call_id"] == "t1" and sent[3]["tool_call"]["result"] == '{"available": true}'
    full = json.loads(post.calls.last.request.content)
    assert full["external_id"] == "conv_1" and full["release"] == 3 and full["metadata"] == {"disconnect_reason": "user"}
    assert [t["role"] for t in full["turns"]] == ["agent", "user", "tool"] and len(full["tool_calls"]) == 1
    assert guidance == []  # reserved


@respx.mock
def test_a_failed_final_post_never_raises():
    respx.get(f"{URL}/agents/agent_123?platform=elevenlabs").mock(return_value=httpx.Response(200, json=AGENT))
    respx.post(f"{URL}/runtime/served").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{URL}/sessions/open").mock(return_value=httpx.Response(201, json={"id": "s1", "created": True}))
    respx.post(f"{URL}/sessions/elevenlabs/conv_2/events").mock(return_value=httpx.Response(200, json={"accepted": 1, "duplicates": 0}))
    post = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(401, json={"detail": "invalid ingest token"}))

    async def call():
        s = await elevenlabs_session(client(), "agent_123", stream=True)
        s.turn("user", "Hi")
        await s.end("agent", conversation_id="conv_2")

    asyncio.run(call())
    assert post.call_count == 1
