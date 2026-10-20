"""Agent configs: what each session ran with (models, voice, prompt), so Insights can compare an agent's versions.

A session's config is a flat map of settings: `llm`, `stt`, `tts` (each `<provider>/<model>`), `voice`, `prompt` (a
template the team built outside sopops), `version` (the team's own label), and any other setting a team sends. It
comes from three places, later ones winning:

1. **Captured**: the Python SDK reads the `AgentSession`'s models; ElevenLabs and Vapi captures read the agent's
   settings with the stored key.
2. **`config`** in the session payload.
3. **`sopops_*` keys** in the session's metadata: LiveKit participant attributes, ElevenLabs dynamic variables, Vapi
   metadata. `sopops_test` names an A/B test; `sopops_release` and `sopops_agent` keep their meaning and aren't config.

The config hash (`sessions.config_hash`) identifies a version's settings: sessions with the same agent, release and
hash are one version. A prompt is stored once (`prompt_texts`) and its hash goes in the config instead.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

PREFIX = "sopops_"
TEST_KEY = "sopops_test"
NOT_CONFIG = {"sopops_release", "sopops_agent", "sopops_prompt_id", TEST_KEY}  # pins and names, not settings
MAX_KEYS = 32
MAX_VALUE = 1024
MAX_PROMPT = 65536
MAX_TEST = 128
PROMPT = "prompt"
UNKNOWN = {"", "unknown", "none", "null"}

Scalar = str | int | float | bool


def from_metadata(meta: Mapping[str, Any] | None) -> tuple[str | None, dict[str, Scalar]]:
    """(test, config) from `sopops_*` keys in a session's metadata (or a nested `{"sopops": {...}}`)."""
    meta = meta or {}
    flat: dict[str, Any] = {k: v for k, v in meta.items() if isinstance(k, str) and k.startswith(PREFIX)}
    nested = meta.get("sopops")
    if isinstance(nested, Mapping):
        flat.update({PREFIX + k: v for k, v in nested.items() if isinstance(k, str) and PREFIX + k not in flat})
    test = flat.get(TEST_KEY)
    config = {k[len(PREFIX):]: v for k, v in flat.items() if k not in NOT_CONFIG and k[len(PREFIX):] not in ("agent", "release", "test")}
    return (str(test).strip() or None) if isinstance(test, (str, int)) and not isinstance(test, bool) else None, clean(config)


def clean(config: Mapping[str, Any] | None) -> dict[str, Scalar]:
    """Lowercase keys, scalars only, blanks and "unknown" left out, numbers kept as numbers."""
    out: dict[str, Scalar] = {}
    for k, v in (config or {}).items():
        if not isinstance(k, str) or not k.strip() or v is None or isinstance(v, (dict, list, tuple)):
            continue
        if isinstance(v, str):
            v = v.strip()
            if v.lower() in UNKNOWN:
                continue
        out[k.strip().lower()] = v
    return out


def merge(*configs: Mapping[str, Any] | None) -> dict[str, Scalar]:
    """Later configs win, key by key."""
    out: dict[str, Scalar] = {}
    for c in configs:
        out.update(clean(c))
    return out


def fit(config: Mapping[str, Scalar]) -> tuple[dict[str, Scalar], list[str]]:
    """The config within limits, and a warning for each setting left out: a session is never refused over its config."""
    out: dict[str, Scalar] = {}
    warnings: list[str] = []
    for k, v in config.items():
        limit = MAX_PROMPT if k == PROMPT else MAX_VALUE
        if len(out) >= MAX_KEYS:
            warnings.append(f"Config has more than {MAX_KEYS} settings; '{k[:64]}' and the rest were left out.")
            break
        if len(k) > 64 or (isinstance(v, str) and len(v) > limit):
            warnings.append(f"Config '{k[:64]}' is too long (keys up to 64 characters, values up to {limit}); it was left out.")
            continue
        out[k] = v
    return out, warnings


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def stored(config: Mapping[str, Scalar]) -> tuple[dict[str, Scalar], str | None, str | None]:
    """(the config as stored, its hash, the prompt text to keep): a prompt is kept once and its hash goes in the
    config as `prompt`. An empty config has no hash."""
    config = dict(config)
    text = config.pop(PROMPT, None)
    prompt = str(text) if text not in (None, "") else None
    if prompt is not None:
        config[PROMPT] = prompt_hash(prompt)
    return config, config_hash(config), prompt


def config_hash(config: Mapping[str, Scalar]) -> str | None:
    if not config:
        return None
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def model_name(provider: Any, model: Any) -> str | None:
    """`<provider>/<model>` from a platform's two fields; a model already written with its provider stays as is."""
    m = str(model).strip() if model not in (None, "") else ""
    p = str(provider).strip() if provider not in (None, "") else ""
    if not m or m.lower() in UNKNOWN:
        return None
    if "/" in m or not p or p.lower() in UNKNOWN:
        return m
    return f"{p.lower()}/{m}"
