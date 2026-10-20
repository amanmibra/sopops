"""What an ElevenLabs agent or Vapi assistant ran with, for a session's config (see `sopops_server.configs`): its
models and voice, read from the platform's agent settings, with the call's own overrides and `sopops_*` keys on top.

ElevenLabs: `conversation_config.agent.prompt.llm` and `.temperature`, `tts.model_id` and `.voice_id`, `asr.provider`;
a call can override the voice (`conversation_config_override.tts.voice_id`) and the LLM. The agent is read with the
stored API key and kept for a few minutes, since the post-call webhook doesn't carry it.

Vapi: `model.provider`/`model.model`/`model.temperature`, `voice.provider`/`voice.model`/`voice.voiceId`,
`transcriber.provider`/`transcriber.model`, from the assistant on the server message (or read with the stored key),
then `call.assistantOverrides`.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .. import configs
from ..runtime import crypto
from ..runtime import platforms
from ..runtime.platforms import PlatformError
from ..runtime.service import Runtime

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops_server.capture.agent_config")

TTL = 300.0
TIMEOUT = platforms.httpx.Timeout(3.0, connect=1.5)


def _get(d: Any, *path: str) -> Any:
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def elevenlabs(agent: dict | None, override: dict | None = None) -> dict[str, Any]:
    """An ElevenLabs agent's settings (GET /v1/convai/agents/<id>) with a call's `conversation_config_override`."""
    cc = _get(agent, "conversation_config") or {}
    o = override or {}
    voice = _get(o, "tts", "voice_id") or _get(cc, "tts", "voice_id")
    tts_model = _get(o, "tts", "model_id") or _get(cc, "tts", "model_id")
    return configs.clean({
        "llm": configs.model_name(None, _get(o, "agent", "prompt", "llm") or _get(cc, "agent", "prompt", "llm")),
        "temperature": _get(o, "agent", "prompt", "temperature") if _get(o, "agent", "prompt", "temperature") is not None else _get(cc, "agent", "prompt", "temperature"),
        "tts": configs.model_name("elevenlabs", tts_model),
        "voice": voice,
        "stt": _get(cc, "asr", "provider"),
        "language": _get(o, "agent", "language") or _get(cc, "agent", "language"),
    })


def vapi(assistant: dict | None, overrides: dict | None = None) -> dict[str, Any]:
    """A Vapi assistant's settings with a call's `assistantOverrides` (each section replaces the assistant's)."""
    a = dict(assistant or {})
    for section in ("model", "voice", "transcriber"):
        if isinstance(_get(overrides, section), dict):
            a[section] = {**(a.get(section) or {}), **overrides[section]}
    return configs.clean({
        "llm": configs.model_name(_get(a, "model", "provider"), _get(a, "model", "model")),
        "temperature": _get(a, "model", "temperature"),
        "tts": configs.model_name(_get(a, "voice", "provider"), _get(a, "voice", "model")) or _get(a, "voice", "provider"),
        "voice": _get(a, "voice", "voiceId"),
        "stt": configs.model_name(_get(a, "transcriber", "provider"), _get(a, "transcriber", "model")),
    })


class AgentSettings:
    """Platform agents read with the workspace's stored key, kept TTL seconds. Never raises: no key, a refusal or
    a timeout gives None, and the session is kept without those settings."""

    def __init__(self, store: Store) -> None:
        self.runtime = Runtime(store)
        self._items: dict[tuple[str, str, str], tuple[float, dict | None]] = {}

    def get(self, ws: str, platform: str, platform_id: str | None) -> dict | None:
        if not platform_id:
            return None
        hit = self._items.get((ws, platform, platform_id))
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        agent: dict | None = None
        try:
            key = self.runtime._secret(ws, platform, "api_key")
            if key is not None:
                agent = platforms.get_agent(platform, key, platform_id, timeout=TIMEOUT)
        except (PlatformError, crypto.SecretKeyMissing) as e:
            log.info("couldn't read %s agent %s for its settings: %s", platform, platform_id, e)
        self._items[(ws, platform, platform_id)] = (time.monotonic() + TTL, agent)
        return agent
