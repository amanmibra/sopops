"""Agent configs: what each session ran with (models, voice, prompt), so Insights can compare an agent's versions.

A session's config is a map of settings: `llm`, `stt`, `tts` (each `<provider>/<model>`), `voice`, `prompt` (a
template the team built outside sopops), `version` (the team's own label), and any other setting a team sends. A
setting is a scalar, or a whole JSON object or array: a voice pipeline's settings, a page of feature flags, a nested
section. The keys are the team's own, lowercased; nested content is kept as sent. It comes from three places, later
ones winning:

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
import math
from typing import Any, Mapping

PREFIX = "sopops_"
TEST_KEY = "sopops_test"
NOT_CONFIG = {"sopops_release", "sopops_agent", "sopops_prompt_id", TEST_KEY}  # pins and names, not settings
MAX_KEYS = 32
MAX_VALUE = 1024  # characters, a string setting
MAX_PROMPT = 65536  # characters, the prompt
MAX_NESTED = 8192  # characters of JSON (compact, keys sorted), an object or array setting
MAX_DEPTH = 8  # how deep an object or array setting may nest; a flat one is 1
MAX_TEST = 128
_DEEP = 32  # a hard stop while reading nested content, so a pathological payload can't blow the stack
PROMPT = "prompt"
UNKNOWN = {"", "unknown", "none", "null"}

Scalar = str | int | float | bool
# A setting: a scalar, or a whole JSON object or array (a voice pipeline's settings, feature flags, a nested section).
Value = Scalar | list[Any] | dict[str, Any]


def from_metadata(meta: Mapping[str, Any] | None) -> tuple[str | None, dict[str, Value]]:
    """(test, config) from `sopops_*` keys in a session's metadata (or a nested `{"sopops": {...}}`)."""
    meta = meta or {}
    flat: dict[str, Any] = {k: v for k, v in meta.items() if isinstance(k, str) and k.startswith(PREFIX)}
    nested = meta.get("sopops")
    if isinstance(nested, Mapping):
        flat.update({PREFIX + k: v for k, v in nested.items() if isinstance(k, str) and PREFIX + k not in flat})
    test = flat.get(TEST_KEY)
    config = {k[len(PREFIX):]: v for k, v in flat.items() if k not in NOT_CONFIG and k[len(PREFIX):] not in ("agent", "release", "test")}
    return (str(test).strip() or None) if isinstance(test, (str, int)) and not isinstance(test, bool) else None, clean(config)


def _nested(v: Any, depth: int = 0) -> Any:
    """Content inside an object or array setting, kept as sent: keys as strings, anything JSON can't hold (a date, a
    set, an object) and anything past the hard depth stop turned into null, so the shape survives."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if depth >= _DEEP:
        return None
    if isinstance(v, Mapping):
        return {str(k): _nested(x, depth + 1) for k, x in v.items() if isinstance(k, (str, int, float, bool))}
    if isinstance(v, (list, tuple)):
        return [_nested(x, depth + 1) for x in v]
    return None


def clean(config: Mapping[str, Any] | None) -> dict[str, Value]:
    """Lowercase keys, blanks and "unknown" left out, numbers kept as numbers. A setting that is an object or an array
    is kept whole (its own keys as sent, see `_nested`); an empty one reads as a blank and is left out."""
    out: dict[str, Value] = {}
    for k, v in (config or {}).items():
        if not isinstance(k, str) or not k.strip() or v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if v.lower() in UNKNOWN:
                continue
        elif isinstance(v, (Mapping, list, tuple)):
            v = _nested(v)
            if not v:
                continue
        elif isinstance(v, bool) or isinstance(v, int):
            pass
        elif isinstance(v, float):
            if not math.isfinite(v):
                continue
        else:
            continue  # a date, a set, an object: not JSON
        out[k.strip().lower()] = v
    return out


def merge(*configs: Mapping[str, Any] | None) -> dict[str, Value]:
    """Later configs win, key by key: a nested setting is replaced whole, never merged into."""
    out: dict[str, Value] = {}
    for c in configs:
        out.update(clean(c))
    return out


def _depth(v: Any, at: int = 1) -> int:
    """How deep an object or array setting nests; a flat one is 1."""
    if at >= _DEEP:
        return at
    kids = list(v.values()) if isinstance(v, dict) else list(v)
    return max((_depth(x, at + 1) for x in kids if isinstance(x, (dict, list))), default=at)


def _dump(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def fit(config: Mapping[str, Value]) -> tuple[dict[str, Value], list[str]]:
    """The config within limits, and a warning for each setting left out: a session is never refused over its config.

    At most `MAX_KEYS` settings; keys up to 64 characters; a string up to `MAX_VALUE` (a prompt up to `MAX_PROMPT`);
    an object or array up to `MAX_NESTED` characters of JSON and `MAX_DEPTH` levels deep."""
    out: dict[str, Value] = {}
    warnings: list[str] = []
    for k, v in config.items():
        if len(out) >= MAX_KEYS:
            warnings.append(f"Config has more than {MAX_KEYS} settings; '{k[:64]}' and the rest were left out.")
            break
        if len(k) > 64:
            warnings.append(f"Config '{k[:64]}' is too long (keys up to 64 characters); it was left out.")
            continue
        if isinstance(v, (dict, list)):
            if _depth(v) > MAX_DEPTH:
                warnings.append(f"Config '{k[:64]}' nests deeper than {MAX_DEPTH} levels; it was left out.")
                continue
            if len(_dump(v)) > MAX_NESTED:
                warnings.append(f"Config '{k[:64]}' is too large (an object or array up to {MAX_NESTED:,} characters of JSON); it was left out.")
                continue
        elif isinstance(v, str):
            limit = MAX_PROMPT if k == PROMPT else MAX_VALUE
            if len(v) > limit:
                warnings.append(f"Config '{k[:64]}' is too long (values up to {limit:,} characters); it was left out.")
                continue
        out[k] = v
    return out, warnings


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def stored(config: Mapping[str, Value]) -> tuple[dict[str, Value], str | None, str | None]:
    """(the config as stored, its hash, the prompt text to keep): a prompt sent as text is kept once and its hash goes
    in the config as `prompt`. A `prompt` that isn't text stays in the config as it is. An empty config has no hash."""
    config = dict(config)
    text = config.get(PROMPT)
    prompt = text if isinstance(text, str) and text else None
    if prompt is not None:
        config[PROMPT] = prompt_hash(prompt)
    return config, config_hash(config), prompt


def config_hash(config: Mapping[str, Value]) -> str | None:
    """A version's identity: the same content hashes the same, nested or not (JSON with every key sorted, all the way
    down), and any difference in it hashes differently."""
    if not config:
        return None
    return hashlib.sha256(_dump(config).encode()).hexdigest()[:16]


def model_name(provider: Any, model: Any) -> str | None:
    """`<provider>/<model>` from a platform's two fields; a model already written with its provider stays as is."""
    m = str(model).strip() if model not in (None, "") else ""
    p = str(provider).strip() if provider not in (None, "") else ""
    if not m or m.lower() in UNKNOWN:
        return None
    if "/" in m or not p or p.lower() in UNKNOWN:
        return m
    return f"{p.lower()}/{m}"
