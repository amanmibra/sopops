"""One way to call a model: `complete(role, messages, schema)` returns an instance of `schema`, over LiteLLM.

Roles name what a call needs, not a model: `fast` (routing) and `reasoning` (checks). Each maps to a LiteLLM model
string from the environment, `SOPOPS_MODEL_FAST` and `SOPOPS_MODEL_REASONING` (e.g. `anthropic/claude-haiku-4-5`).
Provider keys come from the usual variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...); an OpenAI-compatible
endpoint works for local models (`openai/<model>` with `OPENAI_API_BASE`). LiteLLM is used as a library only, pinned
to an exact version with hashes in uv.lock.

Structured output: native (`response_format=<the Pydantic class>`) when `litellm.supports_response_schema` says the
model has it; otherwise JSON mode with the schema in the prompt. Either way the reply is validated with Pydantic, and
a reply that doesn't validate gets one retry with the error.

Prompt caching is the caller's to place: put `{"cache_control": {"type": "ephemeral"}}` on content blocks. LiteLLM
passes it to Anthropic (and Bedrock, Vertex) and drops it elsewhere.

Tests and other callers swap the implementation: `set_llm(FakeLLM(...))`, or pass an `LLM` where a function takes one.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

Role = Literal["fast", "reasoning"]
ROLE_ENV: dict[str, str] = {"fast": "SOPOPS_MODEL_FAST", "reasoning": "SOPOPS_MODEL_REASONING"}
T = TypeVar("T", bound=BaseModel)

MAX_TOKENS = int(os.environ.get("SOPOPS_MAX_TOKENS", "16000"))
TIMEOUT_S = float(os.environ.get("SOPOPS_LLM_TIMEOUT", "300"))


class NotConfigured(RuntimeError):
    """No model is set for a role."""


class BadOutput(RuntimeError):
    """The model's reply didn't match the schema, twice."""


class LLM(Protocol):
    def model(self, role: str) -> str | None:
        """The model string for a role, or None if it isn't configured."""

    def complete(self, role: str, messages: list[dict[str, Any]], schema: type[T]) -> T: ...


@dataclass
class Usage:
    """Running totals, for the benchmark and logs."""

    calls: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    by_role: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, role: str, usage: dict[str, float]) -> None:
        self.calls += 1
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "seconds"):
            setattr(self, k, getattr(self, k) + usage.get(k, 0))
        r = self.by_role.setdefault(role, {})
        r["calls"] = r.get("calls", 0) + 1
        for k, v in usage.items():
            r[k] = r.get(k, 0) + v

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class LiteLLM:
    """The real implementation. Models per role come from the environment unless given."""

    def __init__(self, models: dict[str, str | None] | None = None):
        self._models = models
        self.usage = Usage()
        self._lock = threading.Lock()

    def model(self, role: str) -> str | None:
        if self._models is not None and role in self._models:
            return self._models[role] or None
        return os.environ.get(ROLE_ENV.get(role, ""), "").strip() or None

    def complete(self, role: str, messages: list[dict[str, Any]], schema: type[T]) -> T:
        import litellm

        model = self.model(role)
        if not model:
            raise NotConfigured(f"no model for role '{role}': set {ROLE_ENV.get(role, role)}")
        native = _supports_schema(litellm, model)
        msgs = list(messages) if native else [*messages, _json_instruction(schema)]
        fmt: Any = schema if native else {"type": "json_object"}
        last: Exception | None = None
        for attempt in range(2):
            started = time.monotonic()
            try:
                resp = litellm.completion(model=model, messages=msgs, response_format=fmt, max_tokens=MAX_TOKENS, timeout=TIMEOUT_S)
            except litellm.BadRequestError:
                if fmt is None or native:
                    raise
                fmt = None  # some providers reject JSON mode (e.g. it becomes a forced tool call); the prompt still asks for JSON
                resp = litellm.completion(model=model, messages=msgs, max_tokens=MAX_TOKENS, timeout=TIMEOUT_S)
            self._record(role, litellm, resp, time.monotonic() - started)
            text = resp.choices[0].message.content or ""
            try:
                return schema.model_validate_json(_strip_fences(text))
            except ValidationError as e:
                last = e
                with self._lock:
                    self.usage.retries += 1
                msgs = [
                    *msgs,
                    {"role": "assistant", "content": text or "(empty)"},
                    {"role": "user", "content": f"That reply doesn't match the required JSON schema:\n{_short_errors(e)}\nReply again with only the corrected JSON object."},
                ]
        raise BadOutput(f"{model} returned output that doesn't match {schema.__name__}: {_short_errors(last)}")

    def _record(self, role: str, litellm: Any, resp: Any, seconds: float) -> None:
        u = getattr(resp, "usage", None)
        details = getattr(u, "prompt_tokens_details", None)
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:  # an unpriced model
            cost = 0.0
        row = {
            "input_tokens": float(getattr(u, "prompt_tokens", 0) or 0),
            "output_tokens": float(getattr(u, "completion_tokens", 0) or 0),
            "cache_read_tokens": float(getattr(details, "cached_tokens", 0) or 0) if details else 0.0,
            "cache_write_tokens": float(getattr(u, "cache_creation_input_tokens", 0) or 0),
            "cost_usd": cost,
            "seconds": seconds,
        }
        with self._lock:
            self.usage.add(role, row)


Responder = Callable[[str, list[dict[str, Any]], type[BaseModel]], BaseModel | dict[str, Any]]


class FakeLLM:
    """For tests: `responder(role, messages, schema)` returns the schema instance (or a dict validated into it).
    Every call is kept in `calls`."""

    def __init__(self, responder: Responder, models: dict[str, str] | None = None):
        self.responder = responder
        self.models = models if models is not None else {"fast": "fake/fast", "reasoning": "fake/reasoning"}
        self.calls: list[tuple[str, list[dict[str, Any]], type[BaseModel]]] = []
        self.usage = Usage()

    def model(self, role: str) -> str | None:
        return self.models.get(role)

    def complete(self, role: str, messages: list[dict[str, Any]], schema: type[T]) -> T:
        if not self.model(role):
            raise NotConfigured(f"no model for role '{role}'")
        self.calls.append((role, messages, schema))
        self.usage.add(role, {})
        out = self.responder(role, messages, schema)
        return out if isinstance(out, schema) else schema.model_validate(out)


_default: LLM | None = None


def get_llm() -> LLM:
    global _default
    if _default is None:
        _default = LiteLLM()
    return _default


def set_llm(llm: LLM | None) -> None:
    """Replace the process-wide implementation (None: back to LiteLLM from the environment)."""
    global _default
    _default = llm


def complete(role: str, messages: list[dict[str, Any]], schema: type[T]) -> T:
    return get_llm().complete(role, messages, schema)


def configured(llm: LLM | None = None, *roles: str) -> bool:
    llm = llm or get_llm()
    return all(llm.model(r) for r in (roles or tuple(ROLE_ENV)))


def text_of(messages: list[dict[str, Any]]) -> str:
    """Every text in the messages, joined (for fakes and logs)."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [b.get("text", "") for b in c if isinstance(b, dict)]
    return "\n".join(out)


def _supports_schema(litellm: Any, model: str) -> bool:
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:
        return False


def _json_instruction(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "role": "user",
        "content": "Reply with one JSON object, and nothing else, that matches this JSON schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":")),
    }


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _short_errors(e: Exception | None) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:10])
    return str(e)[:500]
