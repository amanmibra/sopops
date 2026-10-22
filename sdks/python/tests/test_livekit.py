"""The LiveKit adapter, with stand-ins for JobContext and AgentSession (livekit isn't installed for these tests)."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import respx

from sopops import SessionStream, Sopops
from sopops.livekit import agent_config, attach, from_livekit

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
    assert body["test"] is None and body["config"] == {}  # this session's models don't say
    assert len(body["turns"]) == 4 and body["tool_calls"][0]["result"] == '{"ok": true}'
    assert post.calls.last.request.headers["Authorization"] == "Bearer tok"


class Model:
    def __init__(self, provider, model, **opts):
        self.provider, self.model, self._opts = provider, model, SimpleNamespace(**opts)


class ModelSession(Session):
    def __init__(self, **parts):
        super().__init__()
        self.llm, self.stt, self.tts = parts.get("llm"), parts.get("stt"), parts.get("tts")


def test_agent_config_reads_the_sessions_models():
    s = ModelSession(llm=Model("livekit", "openai/gpt-4.1-mini"), stt=Model("deepgram", "nova-3"), tts=Model("cartesia", "sonic-2", voice="ava"))
    assert agent_config(s) == {"llm": "openai/gpt-4.1-mini", "stt": "deepgram/nova-3", "tts": "cartesia/sonic-2", "voice": "ava"}
    assert agent_config(ModelSession(llm=Model("unknown", "unknown"))) == {} and agent_config(Session()) == {}
    assert agent_config(ModelSession(llm=Model("api.anthropic.com", "claude-haiku-4-5"))) == {"llm": "anthropic/claude-haiku-4-5"}


@respx.mock
def test_attach_reports_the_models_test_and_config(tmp_path):
    respx.get(f"{URL}/agents/livekit:tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT))
    post = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1"}))
    c, session = ctx(), ModelSession(llm=Model("anthropic", "claude-haiku-4-5"), tts=Model("cartesia", "sonic-2"))

    async def call():
        await attach(c, session, client=Sopops(URL, "tok", cache_dir=tmp_path), test="haiku-vs-gpt", config={"version": "v2", "tts": "cartesia/sonic-3"})
        await c.callbacks[0]("user_left")

    asyncio.run(call())
    body = json.loads(post.calls.last.request.content)
    assert body["test"] == "haiku-vs-gpt" and body["config"] == {"llm": "anthropic/claude-haiku-4-5", "tts": "cartesia/sonic-3", "version": "v2"}


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


# --- streaming --------------------------------------------------------------------------------------


class StreamingSession(Session):
    """Also like AgentSession's event emitter: `on(event, callback)`, with `emit` for the test to drive it."""

    def __init__(self, userdata=None):
        super().__init__(userdata)
        self.handlers = {}

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)
        return callback

    def emit(self, event, payload):
        for callback in self.handlers.get(event, []):
            callback(payload)


def said(role, text, at, interrupted=False):
    item = SimpleNamespace(type="message", role=role, content=[text], created_at=at, interrupted=interrupted)
    return SimpleNamespace(item=item)


def tools_ran(call_id, name, arguments, output, at):
    call = SimpleNamespace(name=name, arguments=arguments, call_id=call_id, created_at=at)
    return SimpleNamespace(function_calls=[call], function_call_outputs=[SimpleNamespace(output=output, is_error=False)])


def routes():
    return (
        respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT)),
        respx.post(f"{URL}/sessions/open").mock(return_value=httpx.Response(201, json={"id": "s1", "created": True})),
        respx.post(f"{URL}/sessions/livekit/AJ_1/events").mock(return_value=httpx.Response(200, json={"accepted": 1, "duplicates": 0})),
        respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1"})),
    )


def sent_events(route):
    return [e for call in route.calls for e in json.loads(call.request.content)["events"]]


@respx.mock
def test_without_stream_nothing_is_sent_until_shutdown(tmp_path):
    _, opened, events, post = routes()
    c, session = ctx(), StreamingSession()

    async def call():
        await attach(c, session, "tonys-pizza", client=Sopops(URL, "tok", cache_dir=tmp_path))
        assert session.handlers == {}
        await c.callbacks[0]("done")

    asyncio.run(call())
    assert not opened.called and not events.called and post.call_count == 1


@respx.mock
def test_stream_sends_turns_and_tool_calls_in_batches_then_the_full_session(tmp_path):
    _, opened, events, post = routes()
    c, session = ctx(), StreamingSession()
    guidance = []

    async def call():
        await attach(c, session, "tonys-pizza", client=Sopops(URL, "tok", cache_dir=tmp_path), stream=True, on_guidance=guidance.append)
        session.emit("conversation_item_added", said("assistant", "Tony's Pizza, how can I help?", 100.0))
        session.emit("conversation_item_added", said("user", "Delivery please.", 104.0))
        session.emit("conversation_item_added", said("user", "Delivery please.", 104.1))  # a repeat: dropped
        session.emit("function_tools_executed", tools_ran("c1", "check_delivery_zone", '{"address": "221 Mercer St"}', '{"in_zone": true}', 106.0))
        await asyncio.sleep(0.7)  # one flush interval
        assert events.call_count == 1  # everything so far went in one batch
        session.emit("conversation_item_added", said("assistant", "You're in the zone.", 108.0, interrupted=True))
        await c.callbacks[0]("user_left")  # sends what's queued, then posts the full session

    asyncio.run(call())
    assert opened.call_count == 1
    body = json.loads(opened.calls.last.request.content)
    assert body["platform"] == "livekit" and body["external_id"] == "AJ_1" and body["agent"] == "tonys-pizza" and body["release"] == 3
    assert body["channel"] == "voice" and body["metadata"] == {"room": "room-1"} and "started_at" in body
    assert opened.calls.last.request.headers["Authorization"] == "Bearer tok"

    sent = sent_events(events)
    assert [e["seq"] for e in sent] == [0, 1, 2, 3, 4] and events.call_count == 2
    assert [e["type"] for e in sent] == ["turn", "turn", "turn", "tool_call", "turn"]
    assert sent[1]["turn"] == {"role": "user", "text": "Delivery please.", "at": 104.0, "interrupted": False}
    assert sent[2]["turn"]["role"] == "tool" and sent[2]["turn"]["name"] == "check_delivery_zone"
    assert sent[3]["tool_call"] == {"name": "check_delivery_zone", "arguments": '{"address": "221 Mercer St"}', "call_id": "c1", "at": 106.0, "result": '{"in_zone": true}', "is_error": False}
    assert sent[4]["turn"]["interrupted"] is True

    full = json.loads(post.calls.last.request.content)  # the full history, as without streaming
    assert full["external_id"] == "AJ_1" and len(full["turns"]) == 4 and full["metadata"]["shutdown_reason"] == "user_left"
    assert guidance == []  # reserved: never called yet


@respx.mock
def test_stream_failures_never_reach_the_agent(tmp_path):
    respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT))
    opened = respx.post(f"{URL}/sessions/open").mock(side_effect=[httpx.Response(503), httpx.Response(201, json={"id": "s1", "created": True})])
    events = respx.post(f"{URL}/sessions/livekit/AJ_1/events").mock(side_effect=httpx.ConnectError("down"))
    post = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1"}))
    c, session = ctx(), StreamingSession()

    async def call():
        await attach(c, session, "tonys-pizza", client=Sopops(URL, cache_dir=tmp_path), stream=True)
        session.emit("conversation_item_added", said("user", "Hi", 1.0))
        session.emit("conversation_item_added", SimpleNamespace(item=None))  # malformed: logged
        await asyncio.sleep(0.7)  # the open fails: this batch is dropped
        session.emit("conversation_item_added", said("assistant", "Hello!", 2.0))
        await c.callbacks[0]("done")  # opens, the events fail, the full post still goes

    asyncio.run(call())
    assert opened.call_count == 2 and events.call_count == 1 and post.call_count == 1
    assert [e["seq"] for e in sent_events(events)] == [1]


def test_a_full_queue_drops_events_and_batches_stay_bounded(tmp_path):
    async def call():
        with respx.mock:
            respx.post(f"{URL}/sessions/open").mock(return_value=httpx.Response(201, json={"id": "s1", "created": True}))
            events = respx.post(f"{URL}/sessions/api/web%20chat%2F1/events").mock(return_value=httpx.Response(200, json={"accepted": 2, "duplicates": 0}))
            stream = SessionStream(Sopops(URL, cache_dir=tmp_path), {"platform": "api", "external_id": "web chat/1", "agent": None}, max_batch=2, max_queue=5)
            assert stream.session == {"platform": "api", "external_id": "web chat/1"}
            for i in range(7):
                stream.turn({"role": "user", "text": str(i)})
            stream.start()
            await stream.aclose()
            stream.turn({"role": "user", "text": "after close"})  # ignored
            return stream, events

    stream, events = asyncio.run(call())
    assert stream.sent == 5 and stream.dropped == 2
    assert [len(json.loads(c.request.content)["events"]) for c in events.calls] == [2, 2, 1]
    assert [e["seq"] for e in sent_events(events)] == [0, 1, 2, 3, 4]


@respx.mock
def test_attach_fills_per_call_values(tmp_path):
    """The caller's name is filled here and never reaches sopops."""
    respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json={
        **AGENT, "prompt": "You are Tony's. It's {{now}}. Greet {{caller}}. Ask about {{special}}."}))
    posted = respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(201, json={"id": "s1", "created": True}))
    c, session = ctx(), Session()

    async def call():
        ins = await attach(c, session, "tonys-pizza", client=Sopops(URL, "tok", cache_dir=tmp_path),
                           variables={"now": "7:15pm", "caller": "Sam"})
        assert ins.text == "You are Tony's. It's 7:15pm. Greet Sam. Ask about {{special}}."
        assert ins.unfilled == ("special",)
        await c.callbacks[0]("user_left")

    asyncio.run(call())
    assert "Sam" not in posted.calls.last.request.content.decode()
