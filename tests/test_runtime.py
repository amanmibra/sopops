"""Runtime delivery: the runtime token on prompt fetches, push on publish to ElevenLabs and Vapi (mocked with
respx; no real keys), platform credentials, and the call-start webhooks with release linking at ingest."""

import hashlib
import hmac
import json
import time

import httpx
import pytest
import respx
from conftest import WS
from cryptography.fernet import Fernet
from sopops_server.errors import platform_id_key, same_ref

EL = "https://api.elevenlabs.io"
VAPI = "https://api.vapi.ai"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("SOPOPS_ELEVENLABS_URL", raising=False)
    monkeypatch.delenv("SOPOPS_VAPI_URL", raising=False)


def platform_files(files):
    """The fixture with luigis-trattoria on ElevenLabs and sakura-sushi on Vapi."""
    return {
        **files,
        "agents/luigis-trattoria.yaml": files["agents/luigis-trattoria.yaml"].replace("livekit: luigis-trattoria", "elevenlabs: agent_luigi"),
        "agents/sakura-sushi.yaml": files["agents/sakura-sushi.yaml"].replace("livekit: sakura-sushi", "vapi: asst_sakura"),
    }


@pytest.fixture
def published(client, files):
    res = client.post(f"{WS}/publish", json={"files": platform_files(files)})
    assert res.status_code == 200, res.text
    return res.json()["release"]["number"]


def el_agent(prompt="old prompt", **kw):
    return {"agent_id": "agent_luigi", "name": "Luigi", "conversation_config": {"agent": {"prompt": {"prompt": prompt, "llm": "gpt-4o", "tool_ids": ["t1"]}}}, **kw}


def vapi_assistant(prompt="old prompt"):
    return {
        "id": "asst_sakura",
        "name": "Sakura",
        "model": {"provider": "openai", "model": "gpt-4o", "temperature": 0.3, "toolIds": ["tool_1"],
                  "messages": [{"role": "system", "content": prompt}, {"role": "assistant", "content": "Hi!"}]},
        "voice": {"provider": "11labs", "voiceId": "v1"},
    }


def keys(client):
    assert client.put(f"{WS}/runtime/platforms/elevenlabs/api-key", json={"value": "el-test-key-123456"}).status_code == 200
    assert client.put(f"{WS}/runtime/platforms/vapi/api-key", json={"value": "vapi-test-key-123456"}).status_code == 200


# --- the runtime token ----------------------------------------------------------------


def test_prompt_fetch_takes_the_runtime_token_once_there_is_one(client, published):
    assert client.get(f"{WS}/agents/tonys-pizza").status_code == 200  # local mode: no token, open
    ingest_only = client.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).json()["token"]
    assert client.get(f"{WS}/agents/tonys-pizza").status_code == 200  # an ingest token doesn't close fetches
    made = client.post(f"{WS}/tokens", json={"scopes": ["runtime"]}).json()
    token = made["token"]
    for path in ("agents/tonys-pizza", "agents/tonys-pizza/prompt"):
        assert client.get(f"{WS}/{path}").status_code == 401
        assert client.get(f"{WS}/{path}", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get(f"{WS}/{path}", headers={"Authorization": f"Bearer {ingest_only}"}).status_code == 403
        assert client.get(f"{WS}/{path}", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get(f"{WS}/agents").status_code == 200  # the list (no prompts) stays open
    client.delete(f"{WS}/tokens/{made['id']}")
    assert client.get(f"{WS}/agents/tonys-pizza").status_code == 200


# --- delivery mode and credentials ------------------------------------------------------


def test_delivery_modes(client, published):
    rows = {d["agent"]: d for d in client.get(f"{WS}/runtime/delivery").json()}
    assert rows["tonys-pizza"] == {"agent": "tonys-pizza", "platform": "livekit", "platform_id": "tonys-pizza", "mode": "pull", "modes": ["pull"]}
    assert rows["luigis-trattoria"]["modes"] == ["pull", "push", "both"]
    assert client.put(f"{WS}/runtime/platforms/livekit/delivery", json={"mode": "push"}).status_code == 422
    assert client.put(f"{WS}/runtime/platforms/elevenlabs/delivery", json={"mode": "sometimes"}).status_code == 422
    assert client.put(f"{WS}/runtime/platforms/elevenlabs/delivery", json={"mode": "both"}).json() == {"mode": "both"}
    rows = {d["agent"]: d["mode"] for d in client.get(f"{WS}/runtime/delivery").json()}
    assert rows["luigis-trattoria"] == "both" and rows["sakura-sushi"] == "pull" and rows["tonys-pizza"] == "pull"
    assert {p["platform"]: p["delivery"] for p in client.get(f"{WS}/runtime/platforms").json()} == {"elevenlabs": "both", "vapi": "pull"}


def test_older_per_agent_delivery_is_read_until_the_platform_has_its_own(client, published):
    from sqlmodel import Session

    from sopops_server.orm import AgentDelivery

    store = client.app.state.store
    with Session(store.engine) as db:
        db.add(AgentDelivery(workspace="demo", agent="sakura-sushi", mode="push"))
        db.commit()
    assert {p["platform"]: p["delivery"] for p in client.get(f"{WS}/runtime/platforms").json()}["vapi"] == "push"
    client.put(f"{WS}/runtime/platforms/vapi/delivery", json={"mode": "pull"})
    assert {p["platform"]: p["delivery"] for p in client.get(f"{WS}/runtime/platforms").json()}["vapi"] == "pull"
    with Session(store.engine) as db:
        assert db.get(AgentDelivery, ("demo", "sakura-sushi")) is None


def test_platform_keys_are_write_only_and_encrypted(client, published, monkeypatch):
    res = client.put(f"{WS}/runtime/platforms/vapi/api-key", json={"value": "vapi-secret-key-abcd"})
    assert res.status_code == 200 and res.json()["hint"] == "…abcd" and res.json()["secret"] is None
    listed = client.get(f"{WS}/runtime/platforms").json()
    assert "vapi-secret-key" not in json.dumps(listed)
    vapi = next(p for p in listed if p["platform"] == "vapi")
    assert vapi["api_key"]["set"] and not vapi["webhook_secret"]["set"]

    store = client.app.state.store
    with store.engine.connect() as conn:
        stored = conn.exec_driver_sql("SELECT ciphertext FROM platform_secrets").scalar_one()
    assert "vapi-secret-key" not in stored

    made = client.put(f"{WS}/runtime/platforms/vapi/webhook-secret", json={}).json()
    assert made["secret"].startswith("whsec_")  # generated, shown once
    assert client.put(f"{WS}/runtime/platforms/vapi/api-key", json={}).status_code == 400
    assert client.put(f"{WS}/runtime/platforms/retell/api-key", json={"value": "x"}).status_code == 422

    assert client.delete(f"{WS}/runtime/platforms/vapi/api-key").status_code == 204
    assert not next(p for p in client.get(f"{WS}/runtime/platforms").json() if p["platform"] == "vapi")["api_key"]["set"]

    monkeypatch.delenv("SOPOPS_SECRET_KEY")
    assert client.put(f"{WS}/runtime/platforms/vapi/api-key", json={"value": "k" * 20}).status_code == 503


@respx.mock
def test_testing_a_key_reads_one_agent(client, published):
    keys(client)
    route = respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=el_agent("hello")))
    res = client.post(f"{WS}/runtime/platforms/elevenlabs/test", json={}).json()
    assert res["ok"] and res["platform_id"] == "agent_luigi" and "Luigi" in res["message"]
    assert route.calls.last.request.headers["xi-api-key"] == "el-test-key-123456"

    respx.get(f"{VAPI}/assistant/asst_other").mock(return_value=httpx.Response(401, json={"message": "Invalid key"}))
    res = client.post(f"{WS}/runtime/platforms/vapi/test", json={"platform_id": "asst_other"}).json()
    assert res == {"ok": False, "platform_id": "asst_other", "message": "Vapi refused the API key (401)."}
    assert client.post(f"{WS}/runtime/platforms/elevenlabs/test", json={}).status_code == 200
    client.delete(f"{WS}/runtime/platforms/vapi/api-key")
    assert client.post(f"{WS}/runtime/platforms/vapi/test", json={}).status_code == 400


# --- push on publish ----------------------------------------------------------------------


def republish(client, files, note):
    changed = platform_files(files)
    changed["instructions/closing.md"] += f"\n{note}\n"
    res = client.post(f"{WS}/publish", json={"files": changed})
    assert res.json()["created"]
    return res.json()["release"]["number"]


@respx.mock
def test_publish_pushes_to_elevenlabs_and_vapi(client, files, published):
    keys(client)
    client.put(f"{WS}/runtime/platforms/elevenlabs/delivery", json={"mode": "push"})
    client.put(f"{WS}/runtime/platforms/vapi/delivery", json={"mode": "both"})
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=el_agent()))
    el_patch = respx.patch(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json={"agent_id": "agent_luigi", "version_id": "ver_7"}))
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant()))
    vapi_patch = respx.patch(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant("new")))

    number = republish(client, files, "Thanks again.")
    release = client.get(f"{WS}/releases/{number}").json()["agents"]

    el_body = json.loads(el_patch.calls.last.request.content)
    assert el_body == {"conversation_config": {"agent": {"prompt": {"prompt": release["luigis-trattoria"]["prompt"]}}}}
    model = json.loads(vapi_patch.calls.last.request.content)["model"]
    assert model["messages"] == [{"role": "system", "content": release["sakura-sushi"]["prompt"]}, {"role": "assistant", "content": "Hi!"}]
    assert model["provider"] == "openai" and model["temperature"] == 0.3 and model["toolIds"] == ["tool_1"]  # kept
    assert vapi_patch.calls.last.request.headers["Authorization"] == "Bearer vapi-test-key-123456"

    pushes = {p["agent"]: p for p in client.get(f"{WS}/releases/{number}/pushes").json()}
    assert set(pushes) == {"luigis-trattoria", "sakura-sushi"}  # LiveKit agents pull
    assert pushes["luigis-trattoria"]["status"] == "ok" and pushes["luigis-trattoria"]["version_id"] == "ver_7"
    assert pushes["sakura-sushi"]["status"] == "ok" and pushes["sakura-sushi"]["error"] is None


@respx.mock
def test_failed_pushes_are_recorded_and_retried(client, files, published):
    keys(client)
    client.put(f"{WS}/runtime/platforms/elevenlabs/delivery", json={"mode": "push"})
    client.put(f"{WS}/runtime/platforms/vapi/delivery", json={"mode": "push"})
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(500, text="boom el-test-key-123456"))
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(side_effect=httpx.ConnectTimeout("slow"))

    number = republish(client, files, "Bye.")
    pushes = {p["agent"]: p for p in client.get(f"{WS}/releases/{number}/pushes").json()}
    assert pushes["luigis-trattoria"]["status"] == "error"
    assert "el-test-key" not in pushes["luigis-trattoria"]["error"]  # a key echoed back is masked
    assert pushes["sakura-sushi"]["error"] == "Couldn't reach Vapi: ConnectTimeout."

    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant()))
    patch = respx.patch(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json={}))
    retried = {p["agent"]: p for p in client.post(f"{WS}/releases/{number}/pushes/retry", json={}).json()}
    assert retried["sakura-sushi"]["status"] == "ok" and retried["sakura-sushi"]["attempts"] == 2
    assert retried["luigis-trattoria"]["status"] == "error" and retried["luigis-trattoria"]["attempts"] == 2
    assert patch.call_count == 1

    # unchanged prompts aren't written again
    live = client.get(f"{WS}/releases/{number}").json()["agents"]["sakura-sushi"]["prompt"]
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant(live)))
    again = {p["agent"]: p for p in client.post(f"{WS}/releases/{number}/pushes/retry", json={"agents": ["sakura-sushi"]}).json()}
    assert again["sakura-sushi"]["status"] == "unchanged" and patch.call_count == 1

    assert client.post(f"{WS}/releases/{published}/pushes/retry", json={}).status_code == 409  # not live


@respx.mock
def test_push_problems_in_words(client, files, published):
    client.put(f"{WS}/runtime/platforms/elevenlabs/delivery", json={"mode": "push"})
    client.put(f"{WS}/runtime/platforms/vapi/delivery", json={"mode": "push"})
    number = republish(client, files, "No keys yet.")
    pushes = {p["agent"]: p["error"] for p in client.get(f"{WS}/releases/{number}/pushes").json()}
    assert pushes == {"luigis-trattoria": "No ElevenLabs API key in Settings.", "sakura-sushi": "No Vapi API key in Settings."}

    keys(client)
    workflow = el_agent(workflow={"nodes": {"start": {"type": "start"}, "n1": {"type": "subagent"}}})
    respx.get(f"{EL}/v1/convai/agents/agent_luigi").mock(return_value=httpx.Response(200, json=workflow))
    two = vapi_assistant()
    two["model"]["messages"].append({"role": "system", "content": "more"})
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=two))
    pushes = {p["agent"]: p["error"] for p in client.post(f"{WS}/releases/{number}/pushes/retry", json={}).json()}
    assert "workflow with 1 node(s)" in pushes["luigis-trattoria"]
    assert "2 system messages" in pushes["sakura-sushi"]


@respx.mock
def test_rollback_pushes_the_older_release(client, files, published):
    keys(client)
    client.put(f"{WS}/runtime/platforms/vapi/delivery", json={"mode": "push"})
    respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant()))
    patch = respx.patch(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json={}))
    republish(client, files, "Newer.")
    assert client.post(f"{WS}/releases/{published}/activate").status_code == 200
    old = client.get(f"{WS}/releases/{published}").json()["agents"]["sakura-sushi"]["prompt"]
    assert json.loads(patch.calls.last.request.content)["model"]["messages"][0]["content"] == old
    assert client.get(f"{WS}/releases/{published}/pushes").json()[0]["status"] == "ok"


def test_pull_only_workspaces_never_call_platforms(client, files, published):
    with respx.mock(assert_all_called=False) as mock:
        republish(client, files, "Quiet.")
        assert mock.calls.call_count == 0


# --- call-start webhooks (pull) ---------------------------------------------------------------


def sign_el(secret: str, raw: bytes, t: int | None = None) -> str:
    t = t or int(time.time())
    return f"t={t},v0=" + hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()


def test_elevenlabs_conversation_init_serves_the_prompt(client, published):
    url = f"{WS}/runtime/elevenlabs/conversation-init"
    body = {"agent_id": "agent_luigi", "call_sid": "CA123", "caller_id": "+15550100", "called_number": "+15550199"}
    res = client.post(url, json=body)  # local mode: open
    assert res.status_code == 200
    out = res.json()
    prompt = client.get(f"{WS}/releases/{published}").json()["agents"]["luigis-trattoria"]["prompt"]
    assert out["type"] == "conversation_initiation_client_data"
    assert out["conversation_config_override"] == {"agent": {"prompt": {"prompt": prompt}}}
    assert out["dynamic_variables"] == {"sopops_release": published, "sopops_agent": "luigis-trattoria"}
    assert client.get(f"{WS}/fetches", params={"agent": "luigis-trattoria"}).json()[0]["release"] == published

    secret = client.put(f"{WS}/runtime/platforms/elevenlabs/webhook-secret", json={"value": "el-hook-secret"}).json()
    assert secret["secret"] is None
    assert client.post(url, json=body).status_code == 401
    raw = json.dumps(body).encode()
    assert client.post(url, content=raw, headers={"ElevenLabs-Signature": sign_el("el-hook-secret", raw), "Content-Type": "application/json"}).status_code == 200
    assert client.post(url, content=raw, headers={"ElevenLabs-Signature": sign_el("wrong", raw)}).status_code == 401
    stale = sign_el("el-hook-secret", raw, int(time.time()) - 3600)
    assert client.post(url, content=raw, headers={"ElevenLabs-Signature": stale}).status_code == 401
    assert client.post(url, json=body, headers={"X-SopOps-Secret": "el-hook-secret"}).status_code == 200
    assert client.post(url, json=body, headers={"Authorization": "Bearer el-hook-secret"}).status_code == 200
    token = client.post(f"{WS}/tokens").json()["token"]
    assert client.post(url, json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 200  # the runtime token works too

    assert client.post(url, json={**body, "agent_id": "agent_nope"}, headers={"X-SopOps-Secret": "el-hook-secret"}).status_code == 404
    pinned = client.post(url, params={"agent": "luigis-trattoria", "release": published}, json={}, headers={"X-SopOps-Secret": "el-hook-secret"})
    assert pinned.json()["dynamic_variables"]["sopops_release"] == published


def test_hosted_sopops_requires_a_webhook_secret(client, published, monkeypatch):
    url = f"{WS}/runtime/elevenlabs/conversation-init"
    body = {"agent_id": "agent_luigi", "conversation_id": "conv_1"}
    monkeypatch.setenv("SOPOPS_REQUIRE_WEBHOOK_SECRET", "true")
    res = client.post(url, json=body)  # no secret and no runtime token: refused, not open
    assert res.status_code == 401 and "SOPOPS_REQUIRE_WEBHOOK_SECRET" in res.json()["detail"]
    assert client.post(f"{WS}/runtime/vapi/assistant-request", json={"message": {"type": "assistant-request"}}).status_code == 401
    client.put(f"{WS}/runtime/platforms/elevenlabs/webhook-secret", json={"value": "el-hook-secret"})
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"X-SopOps-Secret": "el-hook-secret"}).status_code == 200
    ingest_only = client.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).json()["token"]
    assert client.post(url, json=body, headers={"Authorization": f"Bearer {ingest_only}"}).status_code == 401  # needs runtime
    monkeypatch.setenv("SOPOPS_REQUIRE_WEBHOOK_SECRET", "false")
    client.delete(f"{WS}/runtime/platforms/elevenlabs/webhook-secret")
    assert client.post(url, json=body).status_code == 200  # local mode again


@respx.mock
def test_vapi_assistant_request_overrides_the_system_prompt(client, published):
    url = f"{WS}/runtime/vapi/assistant-request"
    req = {"message": {"type": "assistant-request", "call": {"id": "call_1"}, "phoneNumber": {"id": "pn_1"}}}
    prompt = client.get(f"{WS}/releases/{published}").json()["agents"]["sakura-sushi"]["prompt"]

    out = client.post(url, json=req).json()  # no API key: the prompt goes in as a variable only
    assert out == {
        "assistantId": "asst_sakura",
        "assistantOverrides": {
            "variableValues": {"sopops_prompt": prompt, "sopops_release": published, "sopops_agent": "sakura-sushi"},
            "metadata": {"sopops_release": published, "sopops_agent": "sakura-sushi"},
        },
    }

    keys(client)
    read = respx.get(f"{VAPI}/assistant/asst_sakura").mock(return_value=httpx.Response(200, json=vapi_assistant()))
    out = client.post(url, json={"message": {**req["message"], "call": {"id": "call_2"}}}).json()
    model = out["assistantOverrides"]["model"]
    assert model["messages"][0] == {"role": "system", "content": prompt} and model["temperature"] == 0.3
    client.post(url, json={"message": {**req["message"], "call": {"id": "call_3"}}})
    assert read.call_count == 1  # cached

    assert client.post(url, json={"message": {"type": "status-update", "status": "in-progress"}}).json() == {}

    client.put(f"{WS}/runtime/platforms/vapi/webhook-secret", json={"value": "vapi-hook"})
    assert client.post(url, json=req).status_code == 401
    assert client.post(url, json=req, headers={"X-Vapi-Secret": "vapi-hook"}).status_code == 200
    raw = json.dumps(req).encode()
    ts = str(int(time.time() * 1000))
    sig = hmac.new(b"vapi-hook", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    ok = client.post(url, content=raw, headers={"x-signature": sig, "x-timestamp": ts, "Content-Type": "application/json"})
    assert ok.status_code == 200
    assert client.post(url, content=raw, headers={"x-signature": "0" * 64, "x-timestamp": ts}).status_code == 401


def test_sessions_link_to_the_release_a_webhook_served(client, files, published):
    client.post(f"{WS}/runtime/vapi/assistant-request", json={"message": {"type": "assistant-request", "call": {"id": "call_9"}}})
    client.post(f"{WS}/runtime/elevenlabs/conversation-init", json={"agent_id": "agent_luigi", "call_sid": "CA9"})
    newer = republish(client, files, "Later.")
    assert newer != published
    token = client.post(f"{WS}/tokens").json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    def ingest(**kw):
        body = {"external_id": "x", "turns": [{"role": "user", "text": "hi", "offset_s": 1}], **kw}
        res = client.post(f"{WS}/sessions", json=body, headers=auth)
        assert res.status_code == 201, res.text
        return client.get(f"{WS}/sessions/{res.json()['id']}").json()

    assert ingest(platform="vapi", external_id="call_9", agent="asst_sakura")["release_id"] == published
    assert ingest(platform="elevenlabs", external_id="conv_1", metadata={"phone_call": {"call_sid": "CA9"}})["release_id"] == published
    assert ingest(platform="vapi", external_id="call_unknown")["release_id"] is None
    assert ingest(platform="vapi", external_id="call_10", release=newer)["release_id"] == newer  # what the sender says wins


def test_a_client_reports_the_call_its_prompt_served(client, files, published):
    """The TypeScript client's ElevenLabs adapter fetches the prompt itself, then reports the conversation id."""
    body = {"platform": "elevenlabs", "call_id": "conv_42", "agent": "elevenlabs:agent_luigi", "release": published}
    assert client.post(f"{WS}/runtime/served", json=body).json() == {**body, "agent": "luigis-trattoria"}
    assert client.post(f"{WS}/runtime/served", json={**body, "release": 99}).status_code == 404
    token = client.post(f"{WS}/tokens").json()["token"]
    assert client.post(f"{WS}/runtime/served", json=body).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post(f"{WS}/runtime/served", json=body, headers=auth).status_code == 200
    republish(client, files, "Later.")
    res = client.post(f"{WS}/sessions", json={"platform": "elevenlabs", "external_id": "conv_42", "turns": []}, headers=auth)
    assert client.get(f"{WS}/sessions/{res.json()['id']}").json()["release_id"] == published


def test_cors_for_browser_clients(monkeypatch, tmp_path):
    from conftest import SOPC_BIN
    from fastapi.testclient import TestClient

    from sopops_server import Sopc, Store, create_app

    monkeypatch.setenv("SOPOPS_CORS_ORIGINS", "https://app.example.com")
    c = TestClient(create_app(Store(f"sqlite:///{tmp_path / 'c.db'}", Sopc(SOPC_BIN))))
    pre = c.options(f"{WS}/agents/x", headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert pre.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "access-control-allow-origin" not in c.get("/v1/workspaces", headers={"Origin": "https://evil.example"}).headers


def test_an_agent_answers_to_its_own_id_without_the_platform_prefix(client, files):
    """Teams identify a location by their own id, so asking for `42` works, not just `livekit:42`."""
    client.put(f"{WS}/config/agents/copper-wok-harbor", json={
        "platform": "livekit", "platform_id": "42", "context": "The harbor branch.", "blocks": [], "variables": {}})
    assert client.post(f"{WS}/releases").status_code == 200

    by_sopc_id = client.get(f"{WS}/agents/copper-wok-harbor").json()
    assert client.get(f"{WS}/agents/livekit:42").json()["agent"] == by_sopc_id["agent"]
    assert client.get(f"{WS}/agents/42").json()["agent"] == by_sopc_id["agent"]
    assert client.get(f"{WS}/agents/43").status_code == 404


def test_a_platform_query_scopes_a_raw_agent_identifier(client, files):
    """Adapters can send their own raw identifiers even when another platform uses the same one."""
    client.put(f"{WS}/config/agents/copper-wok-harbor", json={
        "platform": "livekit", "platform_id": "42", "context": "The harbor branch.", "blocks": [], "variables": {}})
    client.put(f"{WS}/config/agents/copper-wok-eastside", json={
        "platform": "vapi", "platform_id": "42", "context": "The eastside branch.", "blocks": [], "variables": {}})
    assert client.post(f"{WS}/releases").status_code == 200

    assert client.get(f"{WS}/agents/42").status_code == 422
    assert client.get(f"{WS}/agents/42", params={"platform": "livekit"}).json()["agent"] == "copper-wok-harbor"
    assert client.get(f"{WS}/agents/42", params={"platform": "vapi"}).json()["agent"] == "copper-wok-eastside"


def test_phone_number_identifiers_match_however_they_are_written():
    """Teams that name agents by phone number paste whatever form they have; all of them find the agent."""
    assert same_ref("livekit:+14155550123", "livekit:+1 (415) 555-0123")
    assert same_ref("livekit:+14155550123", "livekit:1-415-555-0123")
    assert same_ref("livekit:+447462577330", "livekit:+44 7462 577330")
    assert platform_id_key("+1 (415) 555-0123") == platform_id_key("+14155550123")


def test_different_numbers_and_ordinary_ids_still_differ():
    """Normalising numbers mustn't collapse two agents into one, or touch sopc ids and UUIDs."""
    assert not same_ref("livekit:+14155550123", "livekit:+14155550124")
    assert not same_ref("livekit:+14155550123", "vapi:+14155550123")
    assert platform_id_key("copper-wok-harbor") != platform_id_key("copperwokharbor")
    assert platform_id_key("8b1f4c2e-0f4a-4d9b-9a3e-7c2f1b5d6e70") == "8b1f4c2e-0f4a-4d9b-9a3e-7c2f1b5d6e70"
