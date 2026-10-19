"""sopops-mcp (in the client SDK, sopops_client.mcp): every tool, against a sopops app in-process and fake platforms.
No network, no real keys."""

import asyncio
import json

import httpx
import pytest
from conftest import WS
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from sopops_client.mcp import install_skill, make_server
from test_onboarding import FIXTURES, sources

from sopops.onboarding.pipeline import extract
from sopops.onboarding.rules import RulesModels

TOOLS = {"get_kickoff", "get_extraction_guide", "list_platform_agents", "get_agent_prompt", "propose_extraction", "check_fidelity", "push_to_sopops", "report_progress", "report_pull_request", "open_pull_request", "write_sopc_project"}


def call(server, name, **args):
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result.content
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def to_app(client: TestClient) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        res = client.request(request.method, request.url.path, params=dict(request.url.params), content=request.content, headers=dict(request.headers))
        return httpx.Response(res.status_code, content=res.content, headers={"content-type": res.headers.get("content-type", "application/json")})

    return httpx.MockTransport(handler)


def prompts():
    return [s.model_dump() for s in sources()]


def built_proposal() -> dict:
    """What the host agent builds from the units (here, the rule-based models)."""
    return extract(sources(), RulesModels()).model_dump(mode="json")


def connected(client):
    started = client.post(f"{WS}/imports/agent", json={"mode": "hosted", "server_url": "http://sopops.test"}).json()
    server = make_server({"SOPOPS_URL": "http://sopops.test", "SOPOPS_TOKEN": started["token"]}, transport=to_app(client))
    return started, server


def test_lists_its_tools():
    tools = asyncio.run(make_server({}).list_tools())
    assert {t.name for t in tools} == TOOLS


def test_without_sopops_only_the_platform_tools_work():
    server = make_server({})
    assert call(server, "report_progress", step="x", message="y")["ok"] is False
    for tool, args in (("get_kickoff", {}), ("get_extraction_guide", {}), ("propose_extraction", {"prompts": prompts()}),
                       ("check_fidelity", {"proposal": {}, "originals": {}}), ("push_to_sopops", {"proposal": {}, "originals": {}})):
        with pytest.raises(ToolError, match="Not connected"):
            call(server, tool, **args)
    with pytest.raises(ToolError, match="ELEVENLABS_API_KEY"):
        call(server, "list_platform_agents", platform="elevenlabs")


def test_extraction_fidelity_and_sopc_project_through_sopops(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, server = connected(client)
    guide = call(server, "get_extraction_guide")
    assert "Preserve wording" in guide["guide"] and guide["connected"] is True and "agents" in guide["proposal_schema"]["properties"]
    assert guide["session"]["workspace"] == "demo"
    host = call(server, "propose_extraction", prompts=prompts())
    assert host["mode"] == "host" and "## How to classify" in host["instructions"]
    assert host["inputs"][0]["id"] == "marios-pizzeria" and host["inputs"][0]["units"][0]["n"] == 1
    assert set(host["originals"]) == {i["id"] for i in host["inputs"]}
    proposal = built_proposal()
    fid = call(server, "check_fidelity", proposal=proposal, originals=proposal["originals"])
    assert [f["ok"] for f in fid["fidelity"]] == [True] * 4
    written = call(server, "write_sopc_project", proposal=proposal, path="sops")
    assert not written["issues"] and not [i for i in written["sopc_validate"] if i["severity"] == "error"]
    assert (tmp_path / "sops" / "sopc.yaml").exists() and (tmp_path / "sops" / "agents" / "golden-wok.yaml").exists()
    assert "vapi:" in (tmp_path / "sops" / "agents" / "golden-wok.yaml").read_text()


def test_push_and_progress_through_sopops(client):
    started, server = connected(client)
    kick = call(server, "get_kickoff")
    assert "# Onboard voice agents into sopops" in kick and "Workspace: `demo`" in kick and "http://sopops.test" in kick
    assert call(server, "report_progress", step="find", message="Found 4 prompts in prompts/")["ok"]
    proposal = built_proposal()
    remote = call(server, "check_fidelity", proposal=proposal, originals=proposal["originals"])
    assert all(f["ok"] for f in remote["fidelity"])
    pushed = call(server, "push_to_sopops", proposal=proposal, originals=proposal["originals"], findings=proposal["findings"])
    assert pushed["pushes"] == 1 and pushed["review"].endswith(f"/w/demo/import/{started['import']['id']}")
    again = call(server, "push_to_sopops", proposal=proposal, originals=proposal["originals"])
    assert again["pushes"] == 2
    got = client.get(f"{WS}/imports/{started['import']['id']}").json()
    assert got["status"] == "ready" and len(got["proposal"]["agents"]) == 4
    assert "Found 4 prompts" in json.dumps(client.get(f"{WS}/imports/{started['import']['id']}/events").json())


def test_onboarding_endpoints_need_the_token(client):
    for method, path, body in (("GET", "/v1/onboarding/kickoff", None), ("GET", "/v1/onboarding/guide", None),
                               ("POST", "/v1/onboarding/extraction", {"prompts": prompts()}), ("POST", "/v1/onboarding/project", {"proposal": {}})):
        assert client.request(method, path, json=body).status_code == 401, path


def test_install_skill_copies_the_servers_skill(client, tmp_path):
    written = install_skill(tmp_path, ["claude", "codex"], "http://sopops.test", transport=to_app(client))
    assert [p.relative_to(tmp_path).as_posix() for p in written] == [".claude/skills/sopops-onboard/SKILL.md", ".agents/skills/sopops-onboard/SKILL.md"]
    assert written[0].read_text().startswith("---\nname: sopops-onboard")


def test_platform_tools_read_with_env_keys():
    def handler(request):
        assert request.headers["xi-api-key"] == "fake-key"
        if request.url.path == "/v1/convai/agents":
            return httpx.Response(200, json={"agents": [{"agent_id": "a1", "name": "Bella Pasta"}]})
        return httpx.Response(200, json={"name": "Bella Pasta", "conversation_config": {"agent": {"prompt": {"prompt": (FIXTURES / "bella-pasta.md").read_text(), "tools": [{"name": "check_reservations"}]}}}})

    server = make_server({"ELEVENLABS_API_KEY": "fake-key"}, platform_transport=httpx.MockTransport(handler))
    assert call(server, "list_platform_agents", platform="elevenlabs") == [{"id": "a1", "name": "Bella Pasta"}]
    agent = call(server, "get_agent_prompt", platform="elevenlabs", id="a1")
    assert agent["tools"] == ["check_reservations"] and agent["prompt"].startswith("Bella Pasta is")
