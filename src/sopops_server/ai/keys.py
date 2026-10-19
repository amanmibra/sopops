"""Each workspace's AI keys and models, over the deployment's defaults.

The deployment sets defaults with environment variables: `TYPESAFE_API_KEY` for Jev, `SOPOPS_MODEL_FAST` and
`SOPOPS_MODEL_REASONING` for the two roles, and the provider's usual key variable (`ANTHROPIC_API_KEY`, ...). A
workspace can replace any of them in Settings (`ai_settings`, keys encrypted with SOPOPS_SECRET_KEY): each value is the
workspace's own when it has one, else the default. A hosted sopops uses its own models by default, and a team can still
bring its keys.

Every key is tried before it's saved: a TypeSafe key with one tiny Jev decision, a provider key by listing its models
(Anthropic, OpenAI, Google) or, for any other LiteLLM provider, with one short completion. Saving models tries each one.

`AIKeys.ai(ws)` is the implementation a workspace's model calls use. When `lm.set_ai` replaced the implementation
(tests, the benchmark), that one serves every workspace.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
from sqlmodel import Session

from ..errors import workspace_id
from ..orm import AISetting
from ..runtime import crypto
from . import lm
from .signatures import KeyCheck

if TYPE_CHECKING:
    from ..store import Store

LIST_TIMEOUT_S = 15.0


class KeyRejected(Exception):
    """A key or model didn't work. The message says why, in words for the person who pasted it."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    env: str | None  # the key variable LiteLLM reads
    prefix: str  # LiteLLM's model prefix
    key_url: str | None
    fast: tuple[str, ...] = ()  # recommended models, best first; the first one the key can use is suggested
    reasoning: tuple[str, ...] = ()


PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "anthropic/", "https://console.anthropic.com/settings/keys",
             ("claude-haiku-4-5",), ("claude-sonnet-5-5", "claude-sonnet-4-5")),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "openai/", "https://platform.openai.com/api-keys",
             ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"), ("gpt-5", "gpt-4.1", "gpt-4o")),
    Provider("gemini", "Google", "GEMINI_API_KEY", "gemini/", "https://aistudio.google.com/apikey",
             ("gemini-2.5-flash",), ("gemini-2.5-pro",)),
    Provider("other", "Other", None, "", "https://docs.litellm.ai/docs/providers"),
]}
TYPESAFE_KEY_URL = "https://docs.typesafe.ai"
TYPESAFE_ENV = "TYPESAFE_API_KEY"
NOT_CHAT = ("embed", "audio", "realtime", "tts", "transcribe", "whisper", "image", "dall-e", "moderation", "search",
            "computer-use", "davinci", "babbage", "aqa", "imagen", "veo", "learnlm")


def provider_of(model: str | None) -> str | None:
    """The provider a LiteLLM model string belongs to (`anthropic/claude-haiku-4-5`: anthropic), or None."""
    if not model:
        return None
    for p in PROVIDERS.values():
        if p.prefix and model.startswith(p.prefix):
            return p.id
    return "other" if "/" in model else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env(name: str | None) -> str | None:
    if not name:
        return None
    return (os.environ.get(name) or "").strip() or None


class AIKeys:
    def __init__(self, store: Store):
        self.store = store
        self._cache: dict[str, tuple[datetime, lm.AI]] = {}
        self._lock = threading.Lock()

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def row(self, workspace: str) -> AISetting | None:
        with self._db() as db:
            return db.get(AISetting, workspace_id(workspace))

    def _save(self, workspace: str, **fields: Any) -> AISetting:
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            row = db.get(AISetting, ws) or AISetting(workspace=ws, updated_at=_now())
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = _now()
            db.add(row)
        return row

    # --- what applies ----------------------------------------------------------------------------------

    def _resolved(self, row: AISetting | None) -> dict[str, Any]:
        """Each value and where it comes from (`team` or `default`), with keys decrypted."""
        team_ts = crypto.decrypt(row.typesafe_ciphertext) if row and row.typesafe_ciphertext else None
        fast = (row.fast_model if row else None) or lm.model_for("fast")
        reasoning = (row.reasoning_model if row else None) or lm.model_for("reasoning")
        provider = (row.provider if row else None) or provider_of(fast)
        team_key = crypto.decrypt(row.provider_ciphertext) if row and row.provider_ciphertext else None
        return {
            "typesafe_key": team_ts or _env(TYPESAFE_ENV),
            "typesafe_source": "team" if team_ts else ("default" if _env(TYPESAFE_ENV) else None),
            "provider": provider,
            "provider_key": team_key,
            "provider_source": "team" if team_key else ("default" if provider and _env(PROVIDERS.get(provider, PROVIDERS["other"]).env) else None),
            "api_base": (row.api_base if row else None),
            "fast": fast,
            "fast_source": "team" if row and row.fast_model else ("default" if fast else None),
            "reasoning": reasoning,
            "reasoning_source": "team" if row and row.reasoning_model else ("default" if reasoning else None),
        }

    def status(self, workspace: str) -> dict[str, Any]:
        row = self.row(workspace)
        r = self._resolved(row)
        return {
            "typesafe": {"source": r["typesafe_source"], "hint": row.typesafe_hint if row and row.typesafe_ciphertext else None},
            "provider": {"id": r["provider"], "source": r["provider_source"],
                         "hint": row.provider_hint if row and row.provider_ciphertext else None, "api_base": r["api_base"]},
            "fast": {"model": r["fast"], "source": r["fast_source"]},
            "reasoning": {"model": r["reasoning"], "source": r["reasoning_source"]},
            "ready": bool(r["typesafe_key"] and r["fast"] and r["reasoning"]),
            "providers": [{"id": p.id, "label": p.label, "lists_models": p.id != "other", "key_url": p.key_url,
                           "has_default_key": bool(_env(p.env))} for p in PROVIDERS.values()],
            "typesafe_key_url": TYPESAFE_KEY_URL,
        }

    def ai(self, workspace: str) -> lm.AI:
        """The implementation for this workspace's model calls."""
        if lm.replaced():
            return lm.get_ai()
        row = self.row(workspace)
        if row is None:
            return lm.get_ai()
        with self._lock:
            hit = self._cache.get(row.workspace)
            if hit and hit[0] == row.updated_at:
                return hit[1]
        r = self._resolved(row)
        ai = lm.Models({"fast": r["fast"], "reasoning": r["reasoning"]}, jev=bool(r["typesafe_key"]) or lm.jev_enabled(),
                       api_base=r["api_base"], api_key=r["provider_key"], jev_api_key=r["typesafe_key"])
        with self._lock:
            self._cache[row.workspace] = (row.updated_at, ai)
        return ai

    # --- changing them ---------------------------------------------------------------------------------

    def set_typesafe(self, workspace: str, key: str) -> dict[str, Any]:
        key = key.strip()
        if not key:
            raise KeyRejected("Paste a TypeSafe API key.")
        try:
            lm.Models({}, jev=True, jev_api_key=key, cache=False).predict(lm.DECIDE, KeyCheck, sentence="It's raining in Boston.")
        except lm.AIError as e:
            raise KeyRejected(f"TypeSafe didn't accept this key: {_short(e)}") from e
        self._save(workspace, typesafe_ciphertext=crypto.encrypt(key), typesafe_hint=crypto.hint(key))
        return self.status(workspace)

    def set_provider(self, workspace: str, provider: str, key: str | None, api_base: str | None = None) -> dict[str, Any]:
        """Use this provider, with this key (or the deployment's, when it has one). Returns the models it offers."""
        p = PROVIDERS.get(provider)
        if p is None:
            raise KeyRejected(f"Unknown provider '{provider}'.")
        key = (key or "").strip() or None
        api_base = (api_base or "").strip() or None
        if key is None and not _env(p.env) and p.id != "other":
            raise KeyRejected(f"Paste your {p.label} API key.")
        models = list_models(p, key or _env(p.env)) if p.id != "other" else []
        fields: dict[str, Any] = {"provider": p.id, "api_base": api_base if p.id == "other" else None}
        if key:
            fields |= {"provider_ciphertext": crypto.encrypt(key), "provider_hint": crypto.hint(key)}
        else:
            fields |= {"provider_ciphertext": None, "provider_hint": None}
        row = self.row(workspace)
        if row is not None and row.provider != p.id:  # models from another provider won't work with this key
            fields |= {"fast_model": None, "reasoning_model": None}
        self._save(workspace, **fields)
        return {"models": models, "suggested": suggest(p, models)}

    def models(self, workspace: str) -> dict[str, Any]:
        """The models the workspace's provider key can use, for picking."""
        r = self._resolved(self.row(workspace))
        p = PROVIDERS.get(r["provider"] or "")
        if p is None or p.id == "other":
            return {"models": [], "suggested": None}
        key = r["provider_key"] or _env(p.env)
        if not key:
            return {"models": [], "suggested": None}
        models = list_models(p, key)
        return {"models": models, "suggested": suggest(p, models)}

    def set_models(self, workspace: str, fast: str, reasoning: str) -> dict[str, Any]:
        """Use these models, after one short call to each."""
        fast, reasoning = fast.strip(), reasoning.strip()
        if not fast or not reasoning:
            raise KeyRejected("Pick a model for each role.")
        row = self.row(workspace)
        r = self._resolved(row)
        trial = lm.Models({"fast": fast, "reasoning": reasoning}, jev=False, api_base=r["api_base"], api_key=r["provider_key"], cache=False)
        for role in dict.fromkeys(("fast", "reasoning")):
            model = trial.model(role)
            try:
                trial.client(role)(messages=[{"role": "user", "content": "Reply with the word ok."}])
            except Exception as e:  # provider errors come in many types
                raise KeyRejected(f"{model} didn't answer: {_short(e)}") from e
        self._save(workspace, fast_model=fast, reasoning_model=reasoning)
        return self.status(workspace)

    def reset(self, workspace: str, what: str) -> dict[str, Any]:
        """Go back to the deployment's default: `typesafe`, `provider` (the key, the base URL and the models) or
        `models`."""
        if what == "typesafe":
            self._save(workspace, typesafe_ciphertext=None, typesafe_hint=None)
        elif what == "provider":
            self._save(workspace, provider=None, provider_ciphertext=None, provider_hint=None, api_base=None, fast_model=None, reasoning_model=None)
        elif what == "models":
            self._save(workspace, fast_model=None, reasoning_model=None)
        else:
            raise KeyRejected(f"Nothing called '{what}' to reset.")
        return self.status(workspace)


def list_models(p: Provider, key: str) -> list[str]:
    """The chat models a key can use, as LiteLLM model strings. KeyRejected if the provider refuses the key."""
    try:
        if p.id == "anthropic":
            res = httpx.get("https://api.anthropic.com/v1/models", params={"limit": 1000},
                            headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=LIST_TIMEOUT_S)
            ids = [m["id"] for m in res.json().get("data", [])] if res.is_success else []
        elif p.id == "openai":
            res = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=LIST_TIMEOUT_S)
            ids = [m["id"] for m in res.json().get("data", [])] if res.is_success else []
            ids = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-"))]
        elif p.id == "gemini":
            res = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1000},
                            headers={"x-goog-api-key": key}, timeout=LIST_TIMEOUT_S)
            ids = [m["name"].removeprefix("models/") for m in res.json().get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", [])] if res.is_success else []
        else:
            return []
    except httpx.HTTPError as e:
        raise KeyRejected(f"Couldn't reach {p.label}: {_short(e)}") from e
    if res.status_code in (401, 403):
        raise KeyRejected(f"{p.label} didn't accept this key.")
    if not res.is_success:
        raise KeyRejected(f"{p.label} answered {res.status_code} when listing models.")
    ids = [i for i in ids if not any(w in i for w in NOT_CHAT)]
    return [p.prefix + i for i in sorted(dict.fromkeys(ids))]


def suggest(p: Provider, models: list[str]) -> dict[str, str] | None:
    """The recommended fast and reasoning models among those offered."""
    if not models:
        return None
    have = set(models)

    def pick(names: tuple[str, ...], small: bool) -> str:
        for n in names:  # exact, else the newest dated release of it (claude-haiku-4-5-20251001)
            if p.prefix + n in have:
                return p.prefix + n
            dated = sorted(m for m in models if m.startswith(p.prefix + n + "-") and m[len(p.prefix + n) + 1:].isdigit())
            if dated:
                return dated[-1]
        for m in models:
            if any(w in m for w in ("mini", "flash", "haiku", "nano", "lite")) == small:
                return m
        return models[0]

    return {"fast": pick(p.fast, True), "reasoning": pick(p.reasoning, False)}


def _short(e: BaseException) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return text[:300]
