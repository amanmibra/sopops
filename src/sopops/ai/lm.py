"""The models behind DSPy signatures: one `dspy.LM` per role, from the same environment as `sopops.llm`.

`fast` and `reasoning` map to LiteLLM model strings (`SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING`; one serves both
if only one is set), so any provider LiteLLM knows works, and so does any OpenAI-compatible endpoint
(`SOPOPS_LLM_API_BASE`). DSPy's LiteLLM engine is used, so LiteLLM prices each call; its response cache is off by
default (`SOPOPS_DSPY_CACHE=on` turns it on), since a benchmark or a re-check should pay for what it asks.

`Models.predict(role, signature, **inputs)` runs one `dspy.Predict` with that role's model and adds the call's
tokens and cost to `usage`. Each call uses its own copy of the LM, so concurrent calls don't share history.
Tests use `FakeModels`, which answers from a function instead of a model.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import dspy
from pydantic import BaseModel, TypeAdapter

from ..llm import (
    MAX_TOKENS,
    RULES,
    TIMEOUT_S,
    LLMError,
    NotConfigured,
    Usage,
    model_for,
)

ROLES = ("fast", "reasoning")


class AI(Protocol):
    usage: Usage

    def model(self, role: str) -> str | None:
        """The model string for a role, or None if it isn't configured."""

    def predict(self, role: str, signature: type[dspy.Signature], **inputs: Any) -> dspy.Prediction: ...


class Models:
    """The real implementation: a `dspy.LM` per role (LiteLLM engine), models from the environment unless given."""

    def __init__(self, models: Mapping[str, str | None] | None = None, api_base: str | None = None, cache: bool | None = None):
        self._models = dict(models) if models is not None else None
        self.api_base = api_base if api_base is not None else os.environ.get("SOPOPS_LLM_API_BASE") or None
        self.cache = cache if cache is not None else os.environ.get("SOPOPS_DSPY_CACHE", "").lower() in ("1", "on", "true")
        self.usage = Usage()
        self._lms: dict[str, dspy.LM] = {}
        self._lock = threading.Lock()

    def model(self, role: str) -> str | None:
        m = self._models.get(role) if self._models is not None and role in self._models else model_for(role)
        return None if not m or m == RULES else m

    def lm(self, role: str) -> dspy.LM:
        model = self.model(role)
        if not model:
            raise NotConfigured(f"no model for role '{role}': set SOPOPS_MODEL_{role.upper()}")
        with self._lock:
            if model not in self._lms:
                extra = {"api_base": self.api_base} if self.api_base else {}
                self._lms[model] = dspy.LM(model, engine="litellm", max_tokens=MAX_TOKENS, cache=self.cache, timeout=TIMEOUT_S, **extra)
            return self._lms[model]

    def predict(self, role: str, signature: type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        lm = self.lm(role).copy()
        started = time.monotonic()
        try:
            with dspy.context(lm=lm):
                return dspy.Predict(signature)(**inputs)
        except Exception as e:  # provider and parse errors come in many types; report them as one
            raise LLMError(f"{lm.model}: {type(e).__name__}: {str(e)[:500]}") from e
        finally:
            self._record(role, lm.history, time.monotonic() - started)

    def _record(self, role: str, history: list[dict[str, Any]], seconds: float) -> None:
        row = {"input_tokens": 0.0, "output_tokens": 0.0, "cache_read_tokens": 0.0, "cache_write_tokens": 0.0, "cost_usd": 0.0, "seconds": seconds}
        for h in history:
            u = h.get("usage") or {}
            details = u.get("prompt_tokens_details") or {}
            row["input_tokens"] += float(u.get("prompt_tokens") or 0)
            row["output_tokens"] += float(u.get("completion_tokens") or 0)
            row["cache_read_tokens"] += float((details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", 0)) or 0)
            row["cache_write_tokens"] += float(u.get("cache_creation_input_tokens") or 0)
            row["cost_usd"] += float(h.get("cost") or 0.0)
        with self._lock:
            self.usage.add(role, row)
            self.usage.retries += max(len(history) - 1, 0)


Responder = Callable[[str, type[dspy.Signature], dict[str, Any]], BaseModel | dict[str, Any]]


class FakeModels:
    """For tests: `responder(role, signature, inputs)` returns the output fields (a dict, or a Pydantic model whose
    fields are the outputs). Every call is kept in `calls`."""

    def __init__(self, responder: Responder, models: Mapping[str, str] | None = None):
        self.responder = responder
        self.models = dict(models) if models is not None else {"fast": "fake/fast", "reasoning": "fake/reasoning"}
        self.calls: list[tuple[str, type[dspy.Signature], dict[str, Any]]] = []
        self.usage = Usage()

    def model(self, role: str) -> str | None:
        return self.models.get(role)

    def predict(self, role: str, signature: type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        if not self.model(role):
            raise NotConfigured(f"no model for role '{role}'")
        self.calls.append((role, signature, inputs))
        self.usage.add(role, {})
        out = self.responder(role, signature, inputs)
        fields = out.model_dump() if isinstance(out, BaseModel) else dict(out)
        # typed like DSPy's parsed outputs
        return dspy.Prediction(**{name: TypeAdapter(f.annotation).validate_python(fields[name]) for name, f in signature.output_fields.items()})


_default: AI | None = None


def get_ai() -> AI:
    global _default
    if _default is None:
        _default = Models()
    return _default


def set_ai(ai: AI | None) -> None:
    """Replace the process-wide implementation (None: back to `Models` from the environment)."""
    global _default
    _default = ai


def configured(ai: AI | None = None, *roles: str) -> bool:
    ai = ai or get_ai()
    return all(ai.model(r) for r in (roles or ROLES))
