"""LLM calls: `complete(role, messages, schema)` returns an instance of `schema` (a Pydantic model).

Built on the LiteLLM library (never its proxy), so any provider LiteLLM speaks works, including
OpenAI-compatible endpoints for local models. Each role names a model in the environment:

| Role        | Variable                 | Used for |
|-------------|--------------------------|----------|
| `fast`      | `SOPOPS_MODEL_FAST`      | Cheap, short calls (naming variables). |
| `reasoning` | `SOPOPS_MODEL_REASONING` | Judgment calls (splitting prompts, structuring procedures). |

If only one is set, it serves both roles. Models are LiteLLM names (`anthropic/claude-sonnet-4-5`,
`openai/gpt-4.1-mini`, `ollama_chat/qwen3`); provider keys come from the usual variables
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). `SOPOPS_LLM_API_BASE` points every call at another base URL.

Structured output: when LiteLLM says the model supports a response schema, the Pydantic class is passed as
`response_format` and the provider enforces it. Otherwise the schema goes into the system message, JSON mode is
asked for where the model has it, and the reply is validated here, with one retry that shows the model its error.

Code that calls an LLM takes a `Completer` (anything with this `complete` method), so tests pass a fake and
never reach the network.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

Role = Literal["fast", "reasoning"]
Message = dict[str, Any]
T = TypeVar("T", bound=BaseModel)

ENV = {"fast": "SOPOPS_MODEL_FAST", "reasoning": "SOPOPS_MODEL_REASONING"}
TIMEOUT = 180


class LLMError(RuntimeError):
    """The model couldn't be reached, or its reply didn't fit the schema after a retry."""


class NotConfigured(LLMError):
    """No model is set for a role."""


class Completer(Protocol):
    def complete(self, role: Role, messages: list[Message], schema: type[T]) -> T: ...


def model_for(role: Role, env: dict[str, str] | None = None) -> str | None:
    """The model a role uses: its own variable, else the other role's."""
    env = os.environ if env is None else env
    other: Role = "reasoning" if role == "fast" else "fast"
    return (env.get(ENV[role]) or "").strip() or (env.get(ENV[other]) or "").strip() or None


def configured(env: dict[str, str] | None = None) -> bool:
    return model_for("reasoning", env) is not None


class LiteLLM:
    """The default `Completer`, over `litellm.completion`."""

    def __init__(self, models: dict[str, str] | None = None, api_base: str | None = None, timeout: float = TIMEOUT):
        self.models = models or {}
        self.api_base = api_base if api_base is not None else os.environ.get("SOPOPS_LLM_API_BASE") or None
        self.timeout = timeout

    def model(self, role: Role) -> str:
        model = self.models.get(role) or model_for(role)
        if not model:
            raise NotConfigured(f"No model is configured for '{role}' calls; set {ENV['reasoning']} (and optionally {ENV['fast']}).")
        return model

    def complete(self, role: Role, messages: list[Message], schema: type[T]) -> T:
        import litellm  # slow to import; only when a call is made

        litellm.suppress_debug_info = True
        model = self.model(role)
        native = _supports_schema(litellm, model)
        msgs = list(messages) if native else _with_schema(messages, schema)
        kwargs: dict[str, Any] = {"model": model, "timeout": self.timeout, "drop_params": True}
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if native:
            kwargs["response_format"] = schema
        elif _supports_json_mode(litellm, model):
            kwargs["response_format"] = {"type": "json_object"}

        last: Exception | None = None
        for attempt in range(2):
            try:
                reply = litellm.completion(messages=msgs, **kwargs)
            except Exception as e:  # provider errors come in many types; report them as one
                raise LLMError(f"{model}: {type(e).__name__}: {str(e)[:500]}") from e
            content = reply.choices[0].message.content or ""
            try:
                return parse(content, schema)
            except (ValidationError, ValueError) as e:
                last = e
                if attempt == 0:
                    msgs = msgs + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"That reply didn't match the JSON schema:\n{_short(e)}\nReply again with only the corrected JSON object."},
                    ]
        raise LLMError(f"{model}: the reply didn't match {schema.__name__} after a retry: {_short(last)}")


def complete(role: Role, messages: list[Message], schema: type[T], llm: Completer | None = None) -> T:
    """One structured call with the default (environment-configured) model, or with `llm`."""
    return (llm or LiteLLM()).complete(role, messages, schema)


def parse(content: str, schema: type[T]) -> T:
    """The reply as `schema`: a JSON object, possibly inside a ``` fence or surrounded by prose."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("the reply has no JSON object")
        text = text[start : end + 1]
    return schema.model_validate_json(text)


def _supports_schema(litellm: Any, model: str) -> bool:
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:  # unknown models raise; treat them as unsupported
        return False


def _supports_json_mode(litellm: Any, model: str) -> bool:
    try:
        return "response_format" in (litellm.get_supported_openai_params(model=model) or [])
    except Exception:
        return False


def _with_schema(messages: list[Message], schema: type[BaseModel]) -> list[Message]:
    rule = (
        "Reply with one JSON object and nothing else (no prose, no code fence). "
        f"It must validate against this JSON schema:\n{json.dumps(schema.model_json_schema())}"
    )
    if messages and messages[0].get("role") == "system":
        return [{"role": "system", "content": f"{messages[0]['content']}\n\n{rule}"}, *messages[1:]]
    return [{"role": "system", "content": rule}, *messages]


def _short(e: Exception | None) -> str:
    return str(e)[:1500] if e else ""
