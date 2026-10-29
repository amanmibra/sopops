"""sopops-mcp against a fake sopops: it reads platforms and writes files locally, and asks sopops for everything else."""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import respx
import uvicorn
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Implementation

from sopops.mcp import http_app, install_skill, main, make_server

URL = "http://sopops.test"
ENV = {"SOPOPS_URL": URL, "SOPOPS_TOKEN": "sopops_onb_test"}


def call(server, name, **args):
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


@respx.mock
def test_kickoff_names_the_server_and_sends_the_token():
    route = respx.get(f"{URL}/v1/onboarding/kickoff").mock(return_value=httpx.Response(200, json={"instructions": "# Onboard"}))
    assert call(make_server(ENV), "get_kickoff") == "# Onboard"
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sopops_onb_test" and req.url.params["server_url"] == URL


@respx.mock
def test_requests_say_which_coding_agent_is_calling():
    route = respx.get(f"{URL}/v1/onboarding/kickoff").mock(return_value=httpx.Response(200, json={"instructions": "# Onboard"}))

    async def session():
        async with Client(make_server(ENV), client_info=Implementation(name="codex-mcp-client", version="0.50.0")) as c:
            assert not (await c.call_tool("get_kickoff", {})).is_error

    asyncio.run(session())
    headers = route.calls.last.request.headers
    assert (headers["x-sopops-client"], headers["x-sopops-client-version"]) == ("codex-mcp-client", "0.50.0")
    call(make_server(ENV), "get_kickoff")  # no MCP client (a direct call): no client headers
    assert "x-sopops-client" not in route.calls.last.request.headers


@respx.mock
def test_sopops_errors_reach_the_agent_with_their_detail():
    respx.post(f"{URL}/v1/onboarding/fidelity").mock(return_value=httpx.Response(401, json={"detail": "this onboarding token expired"}))
    with pytest.raises(ToolError, match="sopops answered 401: this onboarding token expired"):
        call(make_server(ENV), "check_fidelity", proposal={}, originals={})


def test_not_connected():
    with pytest.raises(ToolError, match="set SOPOPS_URL and SOPOPS_TOKEN"):
        call(make_server({}), "get_kickoff")
    assert call(make_server({}), "report_progress", step="x", message="y")["ok"] is False


@respx.mock
def test_write_sop_project_writes_what_sopops_sends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    files = {"sop.yaml": "variables: {}\n", "agents/a.yaml": "platform: livekit\n"}
    issue = {"code": "x", "message": "m", "path": "sop.yaml", "severity": "warning"}
    respx.post(f"{URL}/v1/onboarding/project").mock(return_value=httpx.Response(200, json={"files": files, "issues": [], "validation": [issue]}))
    out = call(make_server(ENV), "write_sop_project", proposal={}, path="sops")
    assert out["issues"] == [] and out["sop_validate"] == [issue] and len(out["written"]) == 2
    assert out["sopc_validate"] == out["sop_validate"]  # the old field, for one release
    assert (tmp_path / "sops" / "agents" / "a.yaml").read_text() == "platform: livekit\n"


@respx.mock
def test_write_sopc_project_is_a_deprecated_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    files = {"sopc.yaml": "variables: {}\n"}
    respx.post(f"{URL}/v1/onboarding/project").mock(return_value=httpx.Response(200, json={"files": files, "issues": [], "validation": []}))
    out = call(make_server(ENV), "write_sopc_project", proposal={}, path="sops")
    assert out["sopc_validate"] == out["sop_validate"] == [] and (tmp_path / "sops" / "sopc.yaml").exists()


@respx.mock
def test_write_sop_project_stays_in_its_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(f"{URL}/v1/onboarding/project").mock(return_value=httpx.Response(200, json={"files": {"../escape.txt": "x"}, "issues": [], "validation": []}))
    with pytest.raises(ToolError, match="outside"):
        call(make_server(ENV), "write_sop_project", proposal={}, path="sops")
    assert not (tmp_path / "escape.txt").exists()


def test_platform_tools_need_no_server():
    def handler(request):
        assert request.headers["authorization"] == "Bearer vapi-key"
        return httpx.Response(200, json=[{"id": "v1", "name": "Golden Wok"}])

    server = make_server({"VAPI_API_KEY": "vapi-key"}, platform_transport=httpx.MockTransport(handler))
    assert call(server, "list_platform_agents", platform="vapi") == [{"id": "v1", "name": "Golden Wok"}]
    with pytest.raises(ToolError, match="LiveKit agents keep their instructions in code"):
        call(server, "list_platform_agents", platform="livekit")


def skill(request):
    name = request.url.params["name"]
    return httpx.Response(200, json={"name": name, "text": f"---\nname: {name}\n---\n# {name}\n"})


@respx.mock
def test_install_skill(tmp_path):
    respx.get(f"{URL}/v1/onboarding/skill").mock(side_effect=skill)
    written = install_skill(tmp_path, ["codex"], URL)
    assert [p.relative_to(tmp_path).as_posix() for p in written] == [".agents/skills/sopops-onboard/SKILL.md", ".agents/skills/sopops-ab-tests/SKILL.md"]


@respx.mock
def test_an_older_server_without_the_ab_tests_skill(tmp_path):
    respx.get(f"{URL}/v1/onboarding/skill").mock(side_effect=lambda r: skill(r) if r.url.params["name"] == "sopops-onboard" else httpx.Response(404))
    assert len(install_skill(tmp_path, ["claude"], URL)) == 1


@respx.mock
def test_ab_test_guide():
    respx.get(f"{URL}/v1/onboarding/skill").mock(side_effect=skill)
    assert call(make_server(ENV), "get_ab_test_guide") == "# sopops-ab-tests\n"


def test_help(capsys):
    main(["--help"])
    assert "install-skill" in capsys.readouterr().out


@respx.mock
def test_sops_can_be_read_and_saved_long_after_onboarding():
    """With a config token and SOPOPS_WORKSPACE, a coding agent keeps the SOPs up to date. Saves are drafts."""
    env = {"SOPOPS_URL": URL, "SOPOPS_TOKEN": "sopops_cfg", "SOPOPS_WORKSPACE": "prod"}
    ws = f"{URL}/v1/workspaces/prod/config"
    respx.get(ws).mock(return_value=httpx.Response(200, json={
        "agents": [{"id": "tonys-pizza", "version": 2, "platform": "livekit", "blocks": ["take-out"], "context": "…"}],
        "instructions": [], "groups": [],
        "procedures": [{"id": "take-out", "name": "Take-out order", "version": 3, "used_by": [{"agent": "tonys-pizza", "via": None}], "steps": []}],
    }))
    got = call(make_server(env), "list_sops")
    assert [p["name"] for p in got["procedures"]] == ["Take-out order"]
    assert "context" not in got["agents"][0]  # a summary, not every field

    respx.get(f"{ws}/procedures/take-out").mock(return_value=httpx.Response(200, json={"id": "take-out", "name": "Take-out order", "goal": "old"}))
    saved = respx.put(f"{ws}/procedures/take-out").mock(return_value=httpx.Response(200, json={"id": "take-out", "version": 4}))
    one = call(make_server(env), "get_sop", kind="procedures", id="take-out")
    call(make_server(env), "save_sop", kind="procedures", id="take-out", fields={**one, "goal": "Read the order back."}, note="from the repo")
    body = json.loads(saved.calls.last.request.content)
    assert (body["goal"], body["id"], body["note"]) == ("Read the order back.", "take-out", "from the repo")
    assert saved.calls.last.request.headers["authorization"] == "Bearer sopops_cfg"


@respx.mock
def test_sop_management_has_settings_history_and_delete():
    env = {"SOPOPS_URL": URL, "SOPOPS_TOKEN": "sopops_cfg", "SOPOPS_WORKSPACE": "prod"}
    ws = f"{URL}/v1/workspaces/prod/config"
    settings = respx.get(f"{ws}/settings").mock(return_value=httpx.Response(200, json={"variables": {"brand": "Tony's"}, "procedures_heading": "## Procedures"}))
    saved_settings = respx.put(f"{ws}/settings").mock(return_value=httpx.Response(200, json={"item": {}, "changed": []}))
    history = respx.get(f"{ws}/instructions/closing/history").mock(return_value=httpx.Response(200, json=[{"version": 2}]))
    old = respx.get(f"{ws}/instructions/closing/versions/2").mock(return_value=httpx.Response(200, json={"id": "closing", "text": "Thanks."}))
    deleted = respx.delete(f"{ws}/instructions/closing").mock(return_value=httpx.Response(200, json={"workspace": "prod", "changed": []}))

    assert call(make_server(env), "get_sop_settings")["variables"] == {"brand": "Tony's"}
    call(make_server(env), "save_sop_settings", fields={"variables": {"brand": "Tony's Pizza"}, "procedures_heading": "## Procedures"}, note="rename")
    assert json.loads(saved_settings.calls.last.request.content)["note"] == "rename"
    assert call(make_server(env), "get_sop_history", kind="instructions", id="closing") == [{"version": 2}]
    assert call(make_server(env), "get_sop_version", kind="instructions", id="closing", version=2)["text"] == "Thanks."
    call(make_server(env), "delete_sop", kind="instructions", id="closing", note="unused")
    assert deleted.calls.last.request.url.params["note"] == "unused"
    assert settings.called and history.called and old.called


@respx.mock
def test_editing_sops_needs_a_workspace():
    respx.get(f"{URL}/v1/onboarding/session").mock(return_value=httpx.Response(401, json={"detail": "invalid token"}))
    with pytest.raises(ToolError, match="SOPOPS_WORKSPACE"):
        asyncio.run(make_server({"SOPOPS_URL": URL, "SOPOPS_TOKEN": "t"}).call_tool("list_sops", {})).raise_on_error()


# --- remote: Streamable HTTP, each request with its own credentials ------------------------------------------


@pytest.fixture
def remote():
    """`sopops-mcp serve` on a free port, in front of a fake sopops that records each request it gets."""
    seen: list[httpx.Request] = []

    def sopops(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/onboarding/kickoff":
            return httpx.Response(200, json={"instructions": f"# Onboard ({request.url.params['server_url']})"})
        if request.url.path == "/v1/onboarding/project":
            return httpx.Response(200, json={"files": {"sop.yaml": "variables: {}\n"}, "issues": [], "validation": []})
        if request.url.path == "/v1/workspaces/prod/config/settings":
            return httpx.Response(200, json={"variables": {}})
        return httpx.Response(404, json={"detail": "not here"})

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {"SOPOPS_URL": URL, "SOPOPS_APP_URL": "https://sopops.test/app", "SOPOPS_TOKEN": "a-token-the-server-must-not-use"}
    app = http_app(env, httpx.MockTransport(sopops), host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", seen
    server.should_exit = True
    thread.join(5)


def rpc(base, method, params=None, token="sopops_cfg", headers=None, id=1):
    """One JSON-RPC request to /mcp, as an MCP client sends it: the HTTP response and its JSON-RPC message."""
    h = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    res = httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, headers=h, timeout=10)
    if res.status_code != 200:
        return res, None
    if res.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in res.text.splitlines() if line.startswith("data:")]
        return res, json.loads(data[-1])
    return res, res.json()


INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "2.1.0"}}


def test_remote_initialize_and_list_tools(remote):
    base, _ = remote
    res, msg = rpc(base, "initialize", INIT)
    assert res.status_code == 200 and msg["result"]["serverInfo"]["name"] == "sopops"
    _, msg = rpc(base, "tools/list", id=2)
    assert {t["name"] for t in msg["result"]["tools"]} == {t.name for t in asyncio.run(make_server({}).list_tools())}


def test_remote_forwards_each_callers_own_token(remote):
    base, seen = remote
    for token in ("sopops_alice", "sopops_bob"):
        _, msg = rpc(base, "tools/call", {"name": "get_kickoff", "arguments": {}}, token=token)
        assert not msg["result"]["isError"], msg
    assert [r.headers["authorization"] for r in seen] == ["Bearer sopops_alice", "Bearer sopops_bob"]  # never the env's
    assert seen[0].url.params["server_url"] == "https://sopops.test/app"


def test_remote_needs_a_bearer_token(remote):
    base, seen = remote
    for headers in ({}, {"Authorization": "Basic eDp5"}, {"Authorization": "Bearer"}):
        res, _ = rpc(base, "initialize", INIT, token=None, headers=headers)
        assert res.status_code == 401 and res.headers["www-authenticate"].startswith("Bearer")
    assert not seen
    assert httpx.get(f"{base}/healthz").json() == {"ok": True}


def test_remote_workspace_and_platform_keys_come_from_headers(remote):
    base, seen = remote
    _, msg = rpc(base, "tools/call", {"name": "get_sop_settings", "arguments": {}}, headers={"X-SopOps-Workspace": "prod"})
    assert not msg["result"]["isError"] and seen[-1].url.path == "/v1/workspaces/prod/config/settings"
    _, msg = rpc(base, "tools/call", {"name": "list_platform_agents", "arguments": {"platform": "vapi"}})
    assert msg["result"]["isError"] and "x-vapi-api-key header" in msg["result"]["content"][0]["text"]  # not the server's env


def test_remote_write_sop_project_returns_the_files(remote, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base, _ = remote
    for name in ("write_sop_project", "write_sopc_project"):  # the old name still works, for one release
        _, msg = rpc(base, "tools/call", {"name": name, "arguments": {"proposal": {}, "path": "sops"}})
        got = msg["result"]["structuredContent"]
        assert got["files"] == {"sops/sop.yaml": "variables: {}\n"} and got["sop_validate"] == got["sopc_validate"] == []
    assert not list(tmp_path.iterdir())  # nothing written on the server
    _, msg = rpc(base, "tools/call", {"name": "write_sop_project", "arguments": {"proposal": {}, "path": "../elsewhere"}})
    assert msg["result"]["isError"]


def test_remote_ignores_the_servers_own_credentials():
    """A direct call, no request: the remote server has no token, even with SOPOPS_TOKEN in its environment."""
    server = make_server({"SOPOPS_URL": URL, "SOPOPS_TOKEN": "t", "VAPI_API_KEY": "k"}, remote=True)
    with pytest.raises(ToolError, match="Authorization: Bearer"):
        call(server, "get_kickoff")
    with pytest.raises(ToolError, match="x-vapi-api-key"):
        call(server, "list_platform_agents", platform="vapi")
