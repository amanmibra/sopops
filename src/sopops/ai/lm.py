"""The models behind every DSPy signature, by role.

- `fast` and `reasoning`: `dspy.LM` (`engine="auto"`: DSPy's native client first, LiteLLM as its fallback) from
  `SOPOPS_MODEL_FAST` and `SOPOPS_MODEL_REASONING`, e.g. `anthropic/claude-haiku-4-5`. If only one is set it serves
  both. Any provider DSPy knows works, and any OpenAI-compatible endpoint (`SOPOPS_LLM_API_BASE`). Provider keys come
  from the usual variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...).
- `decide`: what answers the decision signatures (outputs that are all `Noul` / `Choice`): TypeSafe's Jev
  (`dspy.experimental.TypeSafe`) when it's on (`TYPESAFE_API_KEY`, or `SOPOPS_JEV=on`; `pip install 'sopops[jev]'`),
  else the fast model, which DSPy asks for probability evidence and decodes the same way.

`Models.predict(role, predictor, **inputs)` runs one `dspy.Predict` (or a signature) with that role's model, on its
own copy of the client, and adds the call's tokens and cost to `usage`; each call is also recorded for the workspace
in scope (`sopops.ai.spend`, through DSPy's usage tracker). DSPy's response cache is in memory only,
unless `SOPOPS_DSPY_CACHE_DIR` names a directory for a disk cache (transcripts would be written there).

`SOPOPS_MODEL_REASONING=rules` is the model-free mode for trying onboarding (`sopops.onboarding.rules.RulesModels`).
Tests use `sopops.ai.fakes`.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import dspy
from dspy.experimental import TypeSafe

from . import dspy_fixes  # noqa: F401  (installs the decision-adapter fix)
from . import spend

ROLES = ("fast", "reasoning")
ROLE_ENV = {"fast": "SOPOPS_MODEL_FAST", "reasoning": "SOPOPS_MODEL_REASONING"}
RULES = "rules"  # not a model: onboarding's model-free mode
DECIDE = "decide"
JEV_MODEL = "jev-1.13.0"  # pinned, not jev-latest, so calibrated thresholds don't move under us
JEV_TIMEOUT_S = 120.0  # the SDK's 10 s default is sized for small requests

MAX_TOKENS = int(os.environ.get("SOPOPS_MAX_TOKENS", "16000"))
TIMEOUT_S = float(os.environ.get("SOPOPS_LLM_TIMEOUT", "300"))


class AIError(RuntimeError):
    """A model couldn't be reached, or its reply couldn't be parsed."""


class NotConfigured(AIError):
    """No model is set for a role."""


def model_for(role: str, env: Mapping[str, str] | None = None) -> str | None:
    """The model a role uses: its own variable, else the other role's (one model can serve both)."""
    env = os.environ if env is None else env
    other = "reasoning" if role == "fast" else "fast"
    return (env.get(ROLE_ENV.get(role, ""), "") or "").strip() or (env.get(ROLE_ENV[other], "") or "").strip() or None


def jev_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    flag = (env.get("SOPOPS_JEV") or "").strip().lower()
    if flag in ("off", "0", "false", "no"):
        return False
    return flag in ("on", "1", "true", "yes") or bool((env.get("TYPESAFE_API_KEY") or "").strip())


@dataclass
class Usage:
    """Running totals per role (Jev's under `jev`), for logs and the benchmark."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    by_role: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, role: str, row: Mapping[str, float]) -> None:
        self.calls += 1
        self.input_tokens += int(row.get("input_tokens", 0))
        self.output_tokens += int(row.get("output_tokens", 0))
        self.cost_usd += float(row.get("cost_usd", 0.0))
        self.seconds += float(row.get("seconds", 0.0))
        r = self.by_role.setdefault(role, {})
        r["calls"] = r.get("calls", 0) + 1
        for k, v in row.items():
            r[k] = r.get(k, 0) + v

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class AI(Protocol):
    usage: Usage

    def model(self, role: str) -> str | None:
        """The model string for a role (`fast`, `reasoning`, `decide`), or None if it isn't configured."""

    def predict(self, role: str, predictor: dspy.Predict | type[dspy.Signature], **inputs: Any) -> dspy.Prediction: ...

    def decision_native(self) -> bool:
        """Whether decisions run on a System One backend (Jev), which answers Choices over long option lists."""


def as_predict(predictor: dspy.Predict | type[dspy.Signature]) -> dspy.Predict:
    return predictor if isinstance(predictor, dspy.Predict) else dspy.Predict(predictor)


_cache_lock = threading.Lock()
_cache_configured = False


def configure_cache() -> None:
    """DSPy's response cache in memory only, unless SOPOPS_DSPY_CACHE_DIR names a disk cache (once per process)."""
    global _cache_configured
    with _cache_lock:
        if _cache_configured:
            return
        folder = os.environ.get("SOPOPS_DSPY_CACHE_DIR") or None
        dspy.configure_cache(enable_disk_cache=bool(folder), disk_cache_dir=folder, enable_memory_cache=True, memory_max_entries=20_000)
        _cache_configured = True


class Models:
    """The real implementation: a `dspy.LM` per role, and Jev for decisions when it's on."""

    def __init__(self, models: Mapping[str, str | None] | None = None, *, jev: bool | None = None, jev_model: str | None = None,
                 api_base: str | None = None):
        configure_cache()
        self._models = dict(models) if models is not None else None
        self.api_base = api_base if api_base is not None else os.environ.get("SOPOPS_LLM_API_BASE") or None
        self.jev = jev_enabled() if jev is None else jev
        self.jev_model = jev_model or os.environ.get("SOPOPS_JEV_MODEL") or JEV_MODEL
        self.usage = Usage()
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    def model(self, role: str) -> str | None:
        if role == DECIDE:
            return self.jev_model if self.jev else self.model("fast")
        m = self._models.get(role) if self._models is not None and role in self._models else model_for(role)
        return None if not m or m == RULES else m

    def decision_native(self) -> bool:
        return self.jev

    def client(self, role: str) -> Any:
        """The role's client: a `dspy.LM`, or for `decide` with Jev on, `dspy.experimental.TypeSafe`."""
        model = self.model(role)
        if not model:
            raise NotConfigured(f"no model for role '{role}': set SOPOPS_MODEL_FAST and SOPOPS_MODEL_REASONING")
        jev = role == DECIDE and self.jev
        key = f"jev:{model}" if jev else model
        with self._lock:
            if key not in self._clients:
                if jev:
                    self._clients[key] = TypeSafe(model, timeout=JEV_TIMEOUT_S)
                else:
                    extra = {"api_base": self.api_base} if self.api_base else {}
                    self._clients[key] = dspy.LM(model, engine="auto", max_tokens=MAX_TOKENS, num_retries=3, timeout=TIMEOUT_S, **extra)
            return self._clients[key]

    def predict(self, role: str, predictor: dspy.Predict | type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        client = self.client(role).copy()
        started = time.monotonic()
        try:
            with dspy.context(lm=client), spend.tracking():
                return as_predict(predictor)(**inputs)
        except Exception as e:  # provider, parse and decoding errors come in many types; report them as one
            raise AIError(f"{self.model(role)}: {type(e).__name__}: {str(e)[:500]}") from e
        finally:
            self._record(role, client, time.monotonic() - started)

    def _record(self, role: str, client: Any, seconds: float) -> None:
        row = {"input_tokens": 0.0, "output_tokens": 0.0, "cost_usd": 0.0, "seconds": seconds}
        jev = isinstance(client, TypeSafe)
        model = self.model(role) if not jev else self.jev_model
        for h in getattr(client, "history", []):
            u = h.get("usage") or {}
            tokens_in = float(u.get("prompt_tokens") or u.get("input_tokens") or 0)
            tokens_out = float(u.get("completion_tokens") or u.get("output_tokens") or 0)
            row["input_tokens"] += tokens_in
            row["output_tokens"] += tokens_out
            row["cost_usd"] += spend.cost(model, int(tokens_in), int(tokens_out)) or float(h.get("cost") or 0.0)
        with self._lock:
            self.usage.add("jev" if jev else role, row)


_default: AI | None = None
_default_lock = threading.Lock()


def get_ai() -> AI:
    """The process-wide implementation, from the environment."""
    global _default
    with _default_lock:
        if _default is None:
            _default = Models()
        return _default


def set_ai(ai: AI | None) -> None:
    """Replace the process-wide implementation (None: back to the environment)."""
    global _default
    with _default_lock:
        _default = ai


def configured(ai: AI | None = None, *roles: str) -> bool:
    """Whether every role (or the given ones) has a model."""
    ai = ai or get_ai()
    return all(ai.model(r) for r in (roles or ROLES))
