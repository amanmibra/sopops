"""Backfill (sopops_server.capture.backfill): the last 7 days of ElevenLabs and Vapi calls, read from the platforms' APIs
(mocked with respx) and converted with the webhooks' own code. Nothing reaches a real platform."""

import json
import time

import httpx
import pytest
import respx
from conftest import SOPC_BIN, WS, fresh_db_url
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_capture import POSTCALL, RecordingJobs, el_signed, post_call_body, sessions, vapi_messages
from test_runtime import platform_files

from sopops_server import Sopc, Store, create_app
from sopops_server.capture import backfill

EL = "https://api.elevenlabs.io"
VAPI = "https://api.vapi.ai"
EL_AGENT = {"agent_id": "agent_luigi", "name": "Luigi", "conversation_config": {
    "agent": {"prompt": {"prompt": "You are Luigi's host.", "llm": "gemini-2.0-flash", "temperature": 0.3}},
    "tts": {"model_id": "eleven_flash_v2_5", "voice_id": "voice_rachel"}, "asr": {"provider": "elevenlabs"}}}
ASSISTANT = {"id": "asst_sakura", "name": "Sakura", "model": {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0.5,
                                                              "messages": [{"role": "system", "content": "You are Sakura's assistant."}]},
             "voice": {"provider": "11labs", "voiceId": "burt", "model": "eleven_turbo_v2_5"}, "transcriber": {"provider": "deepgram", "model": "nova-3"}}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(backfill, "PAUSE_S", 0.0)


HOOKED = "/v1/workspaces/hooked"  # the same agents, whose calls come by webhook, to compare with


@pytest.fixture
def make_app(tmp_path, files):
    def make(keys=("elevenlabs", "vapi")):
        store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
        client = TestClient(create_app(store, RecordingJobs(store)))
        for ws in (WS, HOOKED):
            assert client.post(f"{ws}/publish", json={"files": platform_files(files)}).status_code == 200  # luigis on ElevenLabs, sakura on Vapi
            for p in keys:
                assert client.put(f"{ws}/runtime/platforms/{p}/api-key", json={"value": f"{p}-test-key-123456"}).status_code == 200
        return client

    return make


def conversation(conversation_id, **extra):
    """GET /v1/convai/conversations/<id>: the same object as a post-call webhook's `data`."""
    data = post_call_body(conversation_id)["data"]
    return {**data, "has_audio": True, "has_user_audio": True, "has_response_audio": True, **extra}


def summary(conversation_id, status="done", start=None):
    return {"agent_id": "agent_luigi", "agent_name": "Luigi", "conversation_id": conversation_id, "start_time_unix_secs": start or int(time.time()) - 3600,
            "call_duration_secs": 42, "message_count": 4, "status": status, "call_successful": "success"}


def listing(*items, cursor=None):
    return {"conversations": list(items), "has_more": cursor is not None, "next_cursor": cursor}


def vapi_call(call_id, status="ended", created="2026-10-19T10:00:00.000Z", **extra):
    return {"id": call_id, "orgId": "org_1", "type": "inboundPhoneCall", "assistantId": "asst_sakura", "status": status,
            "createdAt": created, "updatedAt": created, "startedAt": "2026-10-19T10:00:01.000Z", "endedAt": "2026-10-19T10:01:00.000Z",
            "endedReason": "customer-ended-call", "messages": vapi_messages(), "artifact": {"messages": vapi_messages(), "transcript": "AI: Sakura Sushi..."},
            "cost": 0.12, **extra}


def finish(client, platform, start=True, days=None):
    if start:
        res = client.post(f"{WS}/capture/{platform}/backfill", json={"days": days} if days else {})
        assert res.status_code == 202, res.text
    for _ in range(200):
        out = client.get(f"{WS}/capture/{platform}/backfill").json()
        if out["status"] != "running":
            return out
        time.sleep(0.05)
    raise AssertionError("the backfill didn't finish")


def hooked_sessions(client):
    return client.get(f"{HOOKED}/sessions").json()["items"]


SAME = ("platform", "external_id", "agent_id", "agent_ref", "channel", "version_ref", "release_id", "test", "config", "config_hash", "turns", "tool_calls", "metadata",
        "started_at", "ended_at", "status")


def detail(client, s, ws=WS):
    d = client.get(f"{ws}/sessions/{s['id']}").json()
    return {k: d.get(k) for k in SAME}


# --- ElevenLabs ------------------------------------------------------------------------------------------------


@respx.mock
def test_elevenlabs_preview_counts(make_app):
    client = make_app()
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=EL_AGENT))
    client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    raw = json.dumps(post_call_body("conv_1")).encode()
    assert client.post(f"{WS}/capture/elevenlabs/post-call", content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).status_code == 200
    route = respx.get(f"{EL}/v1/convai/conversations").mock(return_value=httpx.Response(200, json=listing(
        summary("conv_1"), summary("conv_2"), summary("conv_3"), summary("conv_live", status="in-progress"))))

    out = client.get(f"{WS}/capture/elevenlabs/backfill/preview").json()
    assert (out["days"], out["key_set"], out["found"], out["already_imported"], out["to_import"]) == (7, True, 3, 1, 2)
    assert out["agents"] == [{"agent": "luigis-trattoria", "platform_id": "agent_luigi", "found": 3, "already_imported": 1, "to_import": 2,
                              "capped": False, "error": None}]  # sakura is on Vapi
    assert out["cost_per_session_usd"] is None and out["estimated_cost_usd"] is None  # nothing spent yet to estimate from
    q = route.calls.last.request.url.params
    assert (q["agent_id"], q["page_size"]) == ("agent_luigi", "100")
    assert abs(int(q["call_start_after_unix"]) - (time.time() - 7 * 86400)) < 60
    assert route.calls.last.request.headers["xi-api-key"] == "elevenlabs-test-key-123456"
    assert client.get(f"{WS}/capture/elevenlabs/backfill").json() is None  # nothing started


@respx.mock
def test_elevenlabs_backfill_makes_the_same_session_as_the_webhook(make_app):
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=EL_AGENT))
    init = {"dynamic_variables": {"system__agent_id": "agent_luigi", "sopops_test": "voices", "sopops_version": "b"},
            "conversation_config_override": {"tts": {"voice_id": "voice_adam"}}}
    body = post_call_body("conv_ab", conversation_initiation_client_data=init)

    client = make_app()
    client.put(f"{HOOKED}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    raw = json.dumps(body).encode()
    assert client.post(f"{HOOKED}/capture/elevenlabs/post-call", content=raw, headers={"ElevenLabs-Signature": el_signed(raw)}).json()["status"] == "created"

    respx.get(f"{EL}/v1/convai/conversations").mock(return_value=httpx.Response(200, json=listing(summary("conv_ab"))))
    one = respx.get(f"{EL}/v1/convai/conversations/conv_ab").mock(return_value=httpx.Response(200, json=conversation("conv_ab", **{
        "conversation_initiation_client_data": init})))
    out = finish(client, "elevenlabs")
    assert (out["status"], out["found"], out["imported"], out["skipped"], out["failed"]) == ("done", 1, 1, 0, 0)
    assert out["agents"][0]["imported"] == 1 and out["finished_at"]
    [a], [b] = hooked_sessions(client), sessions(client)
    assert detail(client, b) == detail(client, a, HOOKED)
    assert b["test"] == "voices" and b["config"]["voice"] == "voice_adam" and b["tool_call_count"] == 1

    again = finish(client, "elevenlabs")  # a second run brings nothing new, and doesn't read the call again
    assert (again["imported"], again["skipped"], one.call_count) == (0, 1, 1)
    assert len(sessions(client)) == 1
    client.put(f"{WS}/capture/elevenlabs/post-call-secret", json={"value": POSTCALL})
    res = client.post(f"{WS}/capture/elevenlabs/post-call", content=raw, headers={"ElevenLabs-Signature": el_signed(raw)})
    assert res.json() == {"status": "unchanged", "session_id": b["id"]}  # the webhook arriving later changes nothing


@respx.mock
def test_elevenlabs_pages_and_the_seven_day_cap(make_app, monkeypatch):
    client = make_app()
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=EL_AGENT))

    def page(request):
        cursor = request.url.params.get("cursor")
        n = {None: 1, "p2": 2, "p3": 3}[cursor]
        return httpx.Response(200, json=listing(summary(f"conv_{n}a"), summary(f"conv_{n}b"), cursor={None: "p2", "p2": "p3", "p3": None}[cursor]))

    route = respx.get(f"{EL}/v1/convai/conversations").mock(side_effect=page)
    respx.get(url__regex=rf"{EL}/v1/convai/conversations/conv_\w+").mock(side_effect=lambda r: httpx.Response(200, json=conversation(r.url.path.rsplit("/", 1)[1])))
    out = client.get(f"{WS}/capture/elevenlabs/backfill/preview", params={"days": 30}).json()
    assert (out["days"], out["found"], out["to_import"]) == (7, 6, 6) and route.call_count == 3
    assert abs(int(route.calls.last.request.url.params["call_start_after_unix"]) - (time.time() - 7 * 86400)) < 60

    monkeypatch.setattr(backfill, "MAX_PAGES", 2)
    out = finish(client, "elevenlabs", days=30)
    assert (out["days"], out["found"], out["imported"]) == (7, 4, 4) and out["agents"][0]["capped"]
    assert sorted(s["external_id"] for s in sessions(client)) == ["conv_1a", "conv_1b", "conv_2a", "conv_2b"]


@respx.mock
def test_platform_errors_are_recorded(make_app):
    client = make_app()
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=EL_AGENT))
    respx.get(f"{EL}/v1/convai/conversations").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json=listing(summary("conv_ok"), summary("conv_gone")))])
    respx.get(f"{EL}/v1/convai/conversations/conv_ok").mock(return_value=httpx.Response(200, json=conversation("conv_ok")))
    respx.get(f"{EL}/v1/convai/conversations/conv_gone").mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    out = finish(client, "elevenlabs")  # a 429 waits and tries again; a call that can't be read is counted, not fatal
    assert (out["status"], out["imported"], out["failed"]) == ("done", 1, 1)
    assert out["agents"][0]["error"].startswith("ElevenLabs answered 404")
    assert [s["external_id"] for s in sessions(client)] == ["conv_ok"]

    respx.get(f"{VAPI}/call").mock(return_value=httpx.Response(401, json={"message": "Invalid Key"}))
    preview = client.get(f"{WS}/capture/vapi/backfill/preview").json()
    assert preview["agents"][0]["error"] == "Vapi refused the API key (401)." and preview["found"] == 0
    out = finish(client, "vapi")
    assert out["status"] == "failed" and out["agents"][0]["error"] == "Vapi refused the API key (401)."
    assert "vapi-test-key" not in json.dumps(out)


def test_agents_without_a_key_are_reported(make_app):
    client = make_app(keys=())
    out = client.get(f"{WS}/capture/elevenlabs/backfill/preview").json()
    assert out["key_set"] is False and out["error"].startswith("No ElevenLabs API key is saved")
    assert [(a["agent"], a["found"], a["error"] == out["error"]) for a in out["agents"]] == [("luigis-trattoria", 0, True)]
    run = finish(client, "vapi")
    assert run["status"] == "failed" and run["error"].startswith("No Vapi API key is saved") and run["agents"][0]["agent"] == "sakura-sushi"
    assert sessions(client) == []


# --- Vapi ------------------------------------------------------------------------------------------------------


@respx.mock
def test_vapi_backfill_makes_the_same_session_as_the_report(make_app, monkeypatch):
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=ASSISTANT))
    overrides = {"model": {"model": "gpt-4.1"}, "metadata": {"sopops_test": "bigger-model"}}
    call = vapi_call("call_1", created="2026-10-19T12:00:00.000Z", assistantOverrides=overrides)

    client = make_app()
    report = backfill.vapi_report(call)
    assert client.post(f"{HOOKED}/runtime/vapi/assistant-request", json={"message": report}).status_code == 200  # the Server URL's way
    monkeypatch.setattr(backfill, "PAGE_SIZE", 2)

    def page(request):
        before = request.url.params.get("createdAtLt")
        calls = {None: [call, vapi_call("call_2", created="2026-10-19T11:00:00.000Z")],
                 "2026-10-19T11:00:00.000Z": [vapi_call("call_3", status="in-progress", created="2026-10-19T10:30:00.000Z")]}[before]
        return httpx.Response(200, json=calls)

    route = respx.get(f"{VAPI}/call").mock(side_effect=page)
    preview = client.get(f"{WS}/capture/vapi/backfill/preview").json()
    assert (preview["found"], preview["to_import"], route.call_count) == (2, 2, 2)  # the running call is left for its report
    q = route.calls[0].request.url.params
    assert q["assistantId"] == "asst_sakura" and q["limit"] == "2" and q["createdAtGt"].endswith("Z")
    assert route.calls[0].request.headers["authorization"] == "Bearer vapi-test-key-123456"

    out = finish(client, "vapi")
    assert (out["status"], out["imported"], out["skipped"]) == ("done", 2, 0)
    [a] = hooked_sessions(client)
    b = next(s for s in sessions(client) if s["external_id"] == "call_1")
    assert detail(client, b) == detail(client, a, HOOKED)
    assert b["test"] == "bigger-model" and b["config"]["llm"] == "openai/gpt-4.1" and b["tool_call_count"] == 1
    assert finish(client, "vapi")["imported"] == 0 and len(sessions(client)) == 2
