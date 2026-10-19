"""Reading agents from ElevenLabs and Vapi for `sopops-mcp`: their names, prompts and tool names. Only GET requests,
read-only. The same as the server's `sopops.onboarding.platforms`, so the MCP server doesn't need the server package.

The API key is passed in per call and used for those requests only: it is never stored, logged or put in an error
message. Base URLs can be overridden with SOPC_ELEVENLABS_URL and SOPC_VAPI_URL (the variables `sopc verify` reads).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

TIMEOUT = 20
PLATFORMS = ("elevenlabs", "vapi")


class PlatformError(RuntimeError):
    """A platform refused the key, didn't have the agent, or couldn't be reached. Never contains the key."""


def base_url(platform: str) -> str:
    if platform == "elevenlabs":
        return os.environ.get("SOPC_ELEVENLABS_URL", "https://api.elevenlabs.io").rstrip("/")
    if platform == "vapi":
        return os.environ.get("SOPC_VAPI_URL", "https://api.vapi.ai").rstrip("/")
    raise PlatformError(f"sopops can't read agents from '{platform}'; paste or upload the prompt instead")


def _client(platform: str, api_key: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    if not api_key or not api_key.strip():
        raise PlatformError(f"enter a {'ElevenLabs' if platform == 'elevenlabs' else 'Vapi'} API key")
    headers = {"xi-api-key": api_key.strip()} if platform == "elevenlabs" else {"Authorization": f"Bearer {api_key.strip()}"}
    return httpx.Client(base_url=base_url(platform), headers=headers, timeout=TIMEOUT, transport=transport)


def _get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        res = client.get(path, params=params)
    except httpx.HTTPError as e:
        raise PlatformError(f"couldn't reach {client.base_url.host}: {type(e).__name__}") from None
    if res.status_code in (401, 403):
        raise PlatformError("the platform refused the API key (check it has read access)")
    if res.status_code == 404:
        raise PlatformError(f"not found: {path}")
    if res.status_code >= 400:
        raise PlatformError(f"{client.base_url.host} answered {res.status_code} for {path}")
    return res.json()


def list_agents(platform: str, api_key: str, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """[{id, name}] for every agent the key can see."""
    with _client(platform, api_key, transport) as c:
        if platform == "elevenlabs":
            out, cursor = [], None
            for _ in range(50):
                body = _get(c, "/v1/convai/agents", {"page_size": 100, **({"cursor": cursor} if cursor else {})})
                out += [{"id": a["agent_id"], "name": a.get("name") or a["agent_id"]} for a in body.get("agents", [])]
                cursor = body.get("next_cursor")
                if not body.get("has_more") or not cursor:
                    break
            return out
        body = _get(c, "/assistant", {"limit": 1000})
        return [{"id": a["id"], "name": a.get("name") or a["id"]} for a in (body if isinstance(body, list) else body.get("results", []))]


def get_agent(platform: str, api_key: str, agent_id: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """{name, platform, platform_id, prompt, tools, notes} for one agent."""
    with _client(platform, api_key, transport) as c:
        return _elevenlabs(c, agent_id) if platform == "elevenlabs" else _vapi(c, agent_id)


def _elevenlabs(c: httpx.Client, agent_id: str) -> dict[str, Any]:
    body = _get(c, f"/v1/convai/agents/{agent_id}")
    prompt_cfg = (((body.get("conversation_config") or {}).get("agent") or {}).get("prompt")) or {}
    tools: list[str] = [t.get("name") for t in prompt_cfg.get("tools") or [] if t.get("name")]
    built_in = prompt_cfg.get("built_in_tools") or {}
    if isinstance(built_in, dict):
        tools += [name for name, cfg in built_in.items() if cfg]
    for tool_id in prompt_cfg.get("tool_ids") or []:
        tool = _get(c, f"/v1/convai/tools/{tool_id}")
        name = (tool.get("tool_config") or {}).get("name")
        if name:
            tools.append(name)
    notes = []
    workflow = body.get("workflow") or {}
    if any((n or {}).get("type") in ("override_agent", "subagent") for n in (workflow.get("nodes") or {}).values()):
        notes.append("This agent is a workflow with subagent nodes; only its main prompt is imported.")
    return {
        "name": body.get("name") or agent_id, "platform": "elevenlabs", "platform_id": agent_id,
        "prompt": prompt_cfg.get("prompt") or "", "tools": list(dict.fromkeys(tools)), "notes": notes,
    }


def _vapi_tool_name(t: dict[str, Any]) -> str | None:
    return ((t.get("function") or {}).get("name")) or t.get("name") or t.get("type")


def _vapi(c: httpx.Client, agent_id: str) -> dict[str, Any]:
    body = _get(c, f"/assistant/{agent_id}")
    model = body.get("model") or {}
    system = [m.get("content") or "" for m in model.get("messages") or [] if m.get("role") == "system"]
    tools = [n for t in model.get("tools") or [] if (n := _vapi_tool_name(t))]
    for tool_id in model.get("toolIds") or []:
        if n := _vapi_tool_name(_get(c, f"/tool/{tool_id}")):
            tools.append(n)
    if body.get("endCallFunctionEnabled"):
        tools.append("endCall")
    return {
        "name": body.get("name") or agent_id, "platform": "vapi", "platform_id": agent_id,
        "prompt": "\n\n".join(s for s in system if s), "tools": list(dict.fromkeys(tools)), "notes": [],
    }
