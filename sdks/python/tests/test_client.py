import asyncio
import json

import httpx
import pytest
import respx

from sopops_client import Sopops, SopopsError, SopopsUnavailable, release_pin

URL = "http://sopops.test/v1/workspaces/prod"
AGENT = {
    "agent": "tonys-pizza", "platform_ref": "livekit:tonys-pizza", "release": 3, "hash": "abc", "prompt": "You are Tony's.",
    "tools": ["check_zone"], "tool_sops": [], "components": [{"kind": "agent", "id": "tonys-pizza", "path": "agents/tonys-pizza.yaml", "version": 2}],
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path):
    return Sopops(URL, "sopops_tok", cache_dir=tmp_path)


@respx.mock
def test_fetch_instructions(client):
    route = respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT))
    ins = run(client.fetch_instructions("tonys-pizza"))
    assert (ins.text, ins.release, ins.agent, ins.source) == ("You are Tony's.", 3, "tonys-pizza", "live")
    assert ins.blocks == AGENT["components"] and ins.tools == ["check_zone"] and ins.platform_ref == "livekit:tonys-pizza"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer sopops_tok" and "release" not in req.url.params

    run(client.fetch_instructions("tonys-pizza", release=2))
    assert route.calls.last.request.url.params["release"] == "2"


@respx.mock
def test_an_outage_uses_the_last_good_copy(client):
    respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(200, json=AGENT))
    run(client.fetch_instructions("tonys-pizza"))

    respx.get(f"{URL}/agents/tonys-pizza").mock(side_effect=httpx.ConnectError("down"))
    ins = run(client.fetch_instructions("tonys-pizza"))
    assert ins.source == "cache" and ins.text == "You are Tony's." and ins.release == 3

    respx.get(f"{URL}/agents/tonys-pizza").mock(return_value=httpx.Response(503, json={"detail": "sopc unavailable"}))
    assert run(client.fetch_instructions("tonys-pizza")).source == "cache"

    # a pinned release is cached on its own
    with pytest.raises(SopopsUnavailable):
        run(client.fetch_instructions("tonys-pizza", release=1))
    ins = run(client.fetch_instructions("tonys-pizza", release=1, fallback="Be helpful."))
    assert ins.source == "fallback" and ins.text == "Be helpful." and ins.release is None


@respx.mock
def test_a_slow_sopops_never_holds_the_call(tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=AGENT)

    respx.get(f"{URL}/agents/tonys-pizza").mock(side_effect=slow)
    client = Sopops(URL, cache_dir=tmp_path, timeout=0.2)
    loop = asyncio.new_event_loop()
    started = loop.time()
    ins = loop.run_until_complete(client.fetch_instructions("tonys-pizza", fallback="Hold on."))
    assert ins.source == "fallback" and loop.time() - started < 2
    loop.close()


@respx.mock
def test_post_session_retries_server_errors(client, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    route = respx.post(f"{URL}/sessions").mock(side_effect=[httpx.Response(502), httpx.ConnectError("x"), httpx.Response(201, json={"id": "s1", "created": True})])
    assert run(client.post_session({"platform": "livekit", "external_id": "j1", "turns": []})) == {"id": "s1", "created": True}
    assert route.call_count == 3
    assert json.loads(route.calls.last.request.content)["external_id"] == "j1"

    respx.post(f"{URL}/sessions").mock(return_value=httpx.Response(401, json={"detail": "invalid ingest token"}))
    with pytest.raises(SopopsError, match="invalid ingest token"):
        run(client.post_session({"platform": "livekit", "external_id": "j1"}))


async def _no_sleep(_):
    return None


def test_release_pin():
    assert release_pin('{"sopops_release": 4}') == 4
    assert release_pin({"sopops": {"release": "5"}}) == 5
    assert release_pin("not json") is None and release_pin(None) is None and release_pin('{"other": 1}') is None


def test_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SOPOPS_URL", URL + "/")
    monkeypatch.setenv("SOPOPS_INGEST_TOKEN", "old-name")
    c = Sopops(cache_dir=tmp_path)
    assert c.url == URL and c.token == "old-name"
    monkeypatch.delenv("SOPOPS_URL")
    with pytest.raises(ValueError):
        Sopops()


@respx.mock
def test_record_served(client):
    route = respx.post(f"{URL}/runtime/served").mock(return_value=httpx.Response(200, json={}))
    run(client.record_served("elevenlabs", "conv_1", "tonys-pizza", 3))
    assert json.loads(route.calls.last.request.content) == {"platform": "elevenlabs", "call_id": "conv_1", "agent": "tonys-pizza", "release": 3}
