"""sopops-mcp against a fake sopops: it reads platforms and writes files locally, and asks sopops for everything else."""

import asyncio
import json

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from sopops_client.mcp import install_skill, main, make_server

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
def test_sopops_errors_reach_the_agent_with_their_detail():
    respx.post(f"{URL}/v1/onboarding/fidelity").mock(return_value=httpx.Response(401, json={"detail": "this onboarding token expired"}))
    with pytest.raises(ToolError, match="sopops answered 401: this onboarding token expired"):
        call(make_server(ENV), "check_fidelity", proposal={}, originals={})


def test_not_connected():
    with pytest.raises(ToolError, match="set SOPOPS_URL and SOPOPS_TOKEN"):
        call(make_server({}), "get_kickoff")
    assert call(make_server({}), "report_progress", step="x", message="y")["ok"] is False


@respx.mock
def test_write_sopc_project_writes_what_sopops_sends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    files = {"sopc.yaml": "variables: {}\n", "agents/a.yaml": "platform: livekit\n"}
    respx.post(f"{URL}/v1/onboarding/project").mock(return_value=httpx.Response(200, json={"files": files, "issues": [], "validation": []}))
    out = call(make_server(ENV), "write_sopc_project", proposal={}, path="sops")
    assert out["issues"] == [] and out["sopc_validate"] == [] and len(out["written"]) == 2
    assert (tmp_path / "sops" / "agents" / "a.yaml").read_text() == "platform: livekit\n"


@respx.mock
def test_write_sopc_project_stays_in_its_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx.post(f"{URL}/v1/onboarding/project").mock(return_value=httpx.Response(200, json={"files": {"../escape.txt": "x"}, "issues": [], "validation": []}))
    with pytest.raises(ToolError, match="outside"):
        call(make_server(ENV), "write_sopc_project", proposal={}, path="sops")
    assert not (tmp_path / "escape.txt").exists()


def test_platform_tools_need_no_server():
    def handler(request):
        assert request.headers["authorization"] == "Bearer vapi-key"
        return httpx.Response(200, json=[{"id": "v1", "name": "Golden Wok"}])

    server = make_server({"VAPI_API_KEY": "vapi-key"}, platform_transport=httpx.MockTransport(handler))
    assert call(server, "list_platform_agents", platform="vapi") == [{"id": "v1", "name": "Golden Wok"}]
    with pytest.raises(ToolError, match="LiveKit agents keep their prompt in code"):
        call(server, "list_platform_agents", platform="livekit")


@respx.mock
def test_install_skill(tmp_path):
    respx.get(f"{URL}/v1/onboarding/skill").mock(return_value=httpx.Response(200, json={"name": "sopops-onboard", "text": "---\nname: sopops-onboard\n---\n"}))
    written = install_skill(tmp_path, ["codex"], URL)
    assert [p.relative_to(tmp_path).as_posix() for p in written] == [".agents/skills/sopops-onboard/SKILL.md"]


def test_help(capsys):
    main(["--help"])
    assert "install-skill" in capsys.readouterr().out
