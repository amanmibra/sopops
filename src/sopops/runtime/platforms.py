"""The ElevenLabs and Vapi APIs, as far as sopops uses them: read one agent, and replace its system prompt.

Where each platform keeps the prompt matches sopc's import and verify:
ElevenLabs `conversation_config.agent.prompt.prompt` (GET/PATCH /v1/convai/agents/<id>, `xi-api-key`);
Vapi the `system` message in `model.messages` (GET/PATCH /assistant/<id>, `Authorization: Bearer`).
Base URLs can be overridden with SOPOPS_ELEVENLABS_URL and SOPOPS_VAPI_URL (tests, proxies).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
NAMES = {"elevenlabs": "ElevenLabs", "vapi": "Vapi"}
PUSHABLE = tuple(NAMES)


class PlatformError(Exception):
    """A failed platform call, in words safe to store and show (never containing the key)."""


@dataclass
class Pushed:
    status: str  # ok | unchanged
    version_id: str | None = None


def base_url(platform: str) -> str:
    env = {"elevenlabs": "SOPOPS_ELEVENLABS_URL", "vapi": "SOPOPS_VAPI_URL"}[platform]
    default = {"elevenlabs": "https://api.elevenlabs.io", "vapi": "https://api.vapi.ai"}[platform]
    return os.environ.get(env, default).rstrip("/")


def _path(platform: str, platform_id: str) -> str:
    seg = quote(platform_id, safe="")
    return f"/v1/convai/agents/{seg}" if platform == "elevenlabs" else f"/assistant/{seg}"


def _headers(platform: str, key: str) -> dict[str, str]:
    return {"xi-api-key": key} if platform == "elevenlabs" else {"Authorization": f"Bearer {key}"}


def _request(platform: str, key: str, method: str, platform_id: str, body: dict | None = None, timeout: httpx.Timeout = TIMEOUT) -> dict:
    name = NAMES[platform]
    try:
        with httpx.Client(base_url=base_url(platform), timeout=timeout) as http:
            res = http.request(method, _path(platform, platform_id), headers=_headers(platform, key), json=body)
    except httpx.HTTPError as e:
        raise PlatformError(f"couldn't reach {name}: {type(e).__name__}") from None
    if res.status_code in (401, 403):
        raise PlatformError(f"{name} refused the API key ({res.status_code})")
    if res.status_code == 404:
        raise PlatformError(f"{name} has no agent '{platform_id}' for this API key (404)")
    if res.status_code >= 400:
        detail = res.text[:300].replace(key, "…") if key else res.text[:300]
        raise PlatformError(f"{name} answered {res.status_code}: {detail}")
    try:
        return res.json() if res.content else {}
    except ValueError:
        raise PlatformError(f"{name} returned a response that isn't JSON") from None


def get_agent(platform: str, key: str, platform_id: str, timeout: httpx.Timeout = TIMEOUT) -> dict:
    """Read one agent (ElevenLabs) or assistant (Vapi). Read-only."""
    return _request(platform, key, "GET", platform_id, timeout=timeout)


def live_prompt(platform: str, agent: dict) -> str | None:
    if platform == "elevenlabs":
        return (((agent.get("conversation_config") or {}).get("agent") or {}).get("prompt") or {}).get("prompt")
    system = [m for m in ((agent.get("model") or {}).get("messages") or []) if m.get("role") == "system"]
    return system[0].get("content") if len(system) == 1 else None


def elevenlabs_routed(agent: dict) -> int:
    """Workflow nodes besides start and end: such an agent doesn't run the main prompt alone (as in sopc verify)."""
    nodes = (agent.get("workflow") or {}).get("nodes") or {}
    return sum(1 for n in nodes.values() if isinstance(n, dict) and n.get("type") not in ("start", "end"))


def vapi_model_with_prompt(assistant: dict, prompt: str) -> dict:
    """The assistant's `model` with its system message replaced (or added first), every other field kept."""
    model = copy.deepcopy(assistant.get("model") or {})
    if not model.get("provider") or not model.get("model"):
        raise PlatformError("the Vapi assistant has no model (provider and model) to put the prompt in")
    messages = list(model.get("messages") or [])
    system = [i for i, m in enumerate(messages) if m.get("role") == "system"]
    if len(system) > 1:
        raise PlatformError(f"the Vapi assistant has {len(system)} system messages; sopops replaces exactly one")
    if system:
        messages[system[0]] = {**messages[system[0]], "content": prompt}
    else:
        messages.insert(0, {"role": "system", "content": prompt})
    model["messages"] = messages
    return model


def push(platform: str, key: str, platform_id: str, prompt: str) -> Pushed:
    """Replace the agent's system prompt with `prompt`: read, change only the prompt, write. Unchanged prompts aren't written."""
    current = get_agent(platform, key, platform_id)
    if platform == "elevenlabs":
        if routed := elevenlabs_routed(current):
            raise PlatformError(f"the ElevenLabs agent is a workflow with {routed} node(s); its main prompt isn't the whole prompt")
        if live_prompt(platform, current) == prompt:
            return Pushed("unchanged", _version(current))
        # A nested PATCH: ElevenLabs merges conversation_config, so only the prompt text changes.
        res = _request(platform, key, "PATCH", platform_id, {"conversation_config": {"agent": {"prompt": {"prompt": prompt}}}})
    else:
        if live_prompt(platform, current) == prompt:
            return Pushed("unchanged", _version(current))
        # Vapi replaces `model` as a whole, so send the full model with only the system message changed.
        res = _request(platform, key, "PATCH", platform_id, {"model": vapi_model_with_prompt(current, prompt)})
    return Pushed("ok", _version(res))


def _version(agent: dict[str, Any]) -> str | None:
    """The platform's version of the agent, when it returns one (ElevenLabs `version_id` with versioning on)."""
    for k in ("version_id", "versionId", "assistantVersion", "version"):
        v = agent.get(k)
        if isinstance(v, (str, int)) and str(v):
            return str(v)
    return None
