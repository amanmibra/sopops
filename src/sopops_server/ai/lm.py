"""The models behind every DSPy signature, by role.

- `fast` and `reasoning`: `dspy.LM` (`engine="auto"`: DSPy's native client first, LiteLLM as its fallback) from
  `SOPOPS_MODEL_FAST` and `SOPOPS_MODEL_REASONING`, e.g. `anthropic/claude-haiku-4-5`. If only one is set it serves
  both. Any provider DSPy knows works, and any OpenAI-compatible endpoint (`SOPOPS_LLM_API_BASE`). Provider keys come
  from the usual variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...).
- `decide`: what answers the decision signatures (outputs that are all `Noul` / `Choice`): a system-1 classifier when
  there is one, else the decision model (`SOPOPS_MODEL_DECISION`, default the fast model), which DSPy asks for
  probability evidence and decodes the same way. The classifier, in order: a team's own TypeSafe key (Jev, called
  directly); the model gateway's system-1 classifier (`SOPOPS_SYSTEM1_URL`, `sopops_server.ai.system1`), which holds
  the deployment's classifier keys; TypeSafe's Jev called directly (`dspy.experimental.TypeSafe`) when it's on
  (`TYPESAFE_API_KEY`, or `SOPOPS_JEV=on`; `pip install 'sopops-server[jev]'`). When the gateway's classifier is
  unavailable, the decision model answers that decision instead.
- `sopops/<name>` (`sopops/sopops-decision`, `sopops/sopops-fast`, `sopops/sopops-reasoning`): a hosted sopops's own
  models, served by its private model gateway at `SOPOPS_LM_URL` (an OpenAI-compatible endpoint, key `SOPOPS_LM_KEY`).
  The gateway decides which model answers each name, so a team only ever sees the sopops name.

`Models.predict(role, predictor, **inputs)` runs one `dspy.Predict` (or a signature) with that role's model, on its
own copy of the client, and adds the call's tokens and cost to `usage`; each call is also recorded for the workspace
in scope (`sopops_server.ai.spend`, through DSPy's usage tracker), and handed to any model-call listeners
(`sopops_server.ai.tracing`), whose request headers go with calls to the model gateway (they make DSPy send the call
through LiteLLM, which carries them). DSPy's response cache is in memory only,
unless `SOPOPS_DSPY_CACHE_DIR` names a directory for a disk cache (transcripts would be written there).

These variables are the deployment's defaults; a workspace's own keys and models replace them (`sopops_server.ai.keys`).

`SOPOPS_MODEL_REASONING=rules` is the model-free mode for trying onboarding (`sopops_server.onboarding.rules.RulesModels`).
Tests use `sopops_server.ai.fakes`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import dspy
from dspy.experimental import TypeSafe

from . import dspy_fixes  # noqa: F401  (installs the decision-adapter fix)
from . import spend, system1, tracing

log = logging.getLogger("sopops_server.ai.lm")

ROLES = ("fast", "reasoning")
ROLE_ENV = {"fast": "SOPOPS_MODEL_FAST", "reasoning": "SOPOPS_MODEL_REASONING"}
DECISION_ENV = "SOPOPS_MODEL_DECISION"
SOPOPS_PREFIX = "sopops/"  # a hosted sopops's own models, through its model gateway
LM_URL_ENV, LM_KEY_ENV = "SOPOPS_LM_URL", "SOPOPS_LM_KEY"
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


def decision_model(env: Mapping[str, str] | None = None) -> str | None:
    """The deployment's decision model when Jev doesn't answer decisions, or None (the fast model answers them)."""
    env = os.environ if env is None else env
    m = (env.get(DECISION_ENV) or "").strip()
    return None if not m or m == RULES else m


def gateway(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """The model gateway's base URL and key, when this deployment has one. `SOPOPS_LM_URL` may be a full URL or a bare
    `host:port` (as a platform's private-network reference gives it), which means `http://host:port/v1`."""
    env = os.environ if env is None else env
    url = (env.get(LM_URL_ENV) or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}/v1"
    return (url, (env.get(LM_KEY_ENV) or "").strip()) if url else None


def jev_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    flag = (env.get("SOPOPS_JEV") or "").strip().lower()
    if flag in ("off", "0", "false", "no"):
        return False
    return flag in ("on", "1", "true", "yes") or bool((env.get("TYPESAFE_API_KEY") or "").strip())


@dataclass
class Usage:
    """Running totals per role (a system-1 classifier's, Jev's or the gateway's, under `jev`), for logs and the benchmark."""

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
        """Whether decisions run on a System One backend (Jev, or the gateway's classifier), which answers Choices over
        long option lists."""


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
    """The real implementation: a `dspy.LM` per role, and a system-1 classifier for decisions when there is one."""

    def __init__(self, models: Mapping[str, str | None] | None = None, *, jev: bool | None = None, jev_model: str | None = None,
                 api_base: str | None = None, api_key: str | None = None, jev_api_key: str | None = None, cache: bool = True,
                 system1_gateway: system1.Gateway | bool | None = None):
        """`api_key` and `jev_api_key` are a workspace's own keys (`ai.keys`); without them the provider's usual
        variables apply. `cache=False` skips DSPy's response cache, whose keys leave out the API key: trying a key
        must reach the provider. `system1_gateway`: the gateway's classifier (None: from the environment, False: not
        used, as for a team's own TypeSafe key); when there is one it answers decisions instead of Jev directly."""
        configure_cache()
        self._models = dict(models) if models is not None else None
        self.api_base = api_base if api_base is not None else os.environ.get("SOPOPS_LLM_API_BASE") or None
        self.api_key = api_key
        self.jev_api_key = jev_api_key
        self.cache = cache
        gw = system1.gateway() if system1_gateway is None or system1_gateway is True else system1_gateway
        self.system1: system1.Gateway | None = gw or None
        self.jev = (jev_enabled() if jev is None else jev) and self.system1 is None
        self.jev_model = jev_model or os.environ.get("SOPOPS_JEV_MODEL") or JEV_MODEL
        self.usage = Usage()
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    def model(self, role: str) -> str | None:
        if role == DECIDE:
            if self.system1 is not None:
                return self.system1.model
            if self.jev:
                return self.jev_model
            return self.decision_fallback()
        m = self._models.get(role) if self._models is not None and role in self._models else model_for(role)
        return None if not m or m == RULES else m

    def decision_fallback(self) -> str | None:
        """The model that answers decisions without a classifier (and when the gateway's is unavailable)."""
        if self._models is not None and DECIDE in self._models:
            return self._models[DECIDE] or self.model("fast")
        return decision_model() or self.model("fast")

    def decision_native(self) -> bool:
        return self.jev or self.system1 is not None

    def client(self, role: str) -> Any:
        """The role's client: a `dspy.LM`, or for `decide`, the classifier's System One client
        (`system1.GatewaySystemOne`, or `dspy.experimental.TypeSafe` with Jev on)."""
        if role == DECIDE and self.system1 is not None:
            key = f"system1:{self.system1.url}:{self.system1.model}"
            with self._lock:
                if key not in self._clients:
                    self._clients[key] = system1.GatewaySystemOne(self.system1, cache=self.cache)
                return self._clients[key]
        model = self.model(role)
        if not model:
            raise NotConfigured(f"no model for role '{role}': set SOPOPS_MODEL_FAST and SOPOPS_MODEL_REASONING")
        jev = role == DECIDE and self.jev
        if jev:
            key = f"jev:{model}"
            with self._lock:
                if key not in self._clients:
                    self._clients[key] = TypeSafe(model, api_key=self.jev_api_key, cache=self.cache, timeout=JEV_TIMEOUT_S)
                return self._clients[key]
        return self.lm(model)

    def lm(self, model: str) -> Any:
        """A `dspy.LM` for a model string (a `sopops/` name goes through the model gateway)."""
        key = model
        with self._lock:
            if key not in self._clients:
                if model.startswith(SOPOPS_PREFIX):
                    gw = gateway()
                    if gw is None:
                        raise NotConfigured(f"{model} needs the model gateway: set {LM_URL_ENV} and {LM_KEY_ENV}")
                    # The gateway speaks OpenAI's API and maps the sopops name to whichever model serves it.
                    self._clients[key] = dspy.LM("openai/" + model.removeprefix(SOPOPS_PREFIX), engine="auto", api_base=gw[0], api_key=gw[1] or "none",
                                                 max_tokens=MAX_TOKENS, num_retries=3, timeout=TIMEOUT_S, cache=self.cache)
                else:
                    extra = {"api_base": self.api_base} if self.api_base else {}
                    if self.api_key:
                        extra["api_key"] = self.api_key
                    self._clients[key] = dspy.LM(model, engine="auto", max_tokens=MAX_TOKENS, num_retries=3, timeout=TIMEOUT_S, cache=self.cache, **extra)
            return self._clients[key]

    def predict(self, role: str, predictor: dspy.Predict | type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        model = self.model(role)
        trace = tracing.start(role, as_predict(predictor).signature, inputs, model)
        try:
            try:
                out = self._run(role, self._with_headers(self.client(role).copy(), model, trace), predictor, inputs)
            except system1.Unavailable as e:
                fallback = self.decision_fallback() if role == DECIDE else None
                if not fallback:
                    raise AIError(f"{self.model(role)}: Unavailable: {str(e)[:500]}") from e
                # As a deployment without a classifier would: the decision model answers, with the same thresholds.
                log.warning("system-1 classifier unavailable, %s answers instead: %s", fallback, str(e)[:300])
                out = self._run(role, self._with_headers(self.lm(fallback).copy(), fallback, trace), predictor, inputs, model=fallback)
        except BaseException as e:
            trace.failed(e)
            raise
        trace.done(out)
        return out

    @staticmethod
    def _with_headers(client: Any, model: str | None, trace: tracing.Trace) -> Any:
        """The client with the listeners' request headers, for a call to the hosted model gateway only."""
        headers = trace.gateway_headers(model)
        if not headers or not isinstance(client, dspy.LM):
            return client
        return client.copy(extra_headers={**(client.kwargs.get("extra_headers") or {}), **headers})

    def _run(self, role: str, client: Any, predictor: dspy.Predict | type[dspy.Signature], inputs: dict[str, Any], model: str | None = None) -> dspy.Prediction:
        started = time.monotonic()
        try:
            with dspy.context(lm=client), spend.tracking():
                return as_predict(predictor)(**inputs)
        except system1.Unavailable:
            raise
        except Exception as e:  # provider, parse and decoding errors come in many types; report them as one
            raise AIError(f"{model or self.model(role)}: {type(e).__name__}: {str(e)[:500]}") from e
        finally:
            self._record(role, client, time.monotonic() - started, model)

    def _record(self, role: str, client: Any, seconds: float, model: str | None = None) -> None:
        row = {"input_tokens": 0.0, "output_tokens": 0.0, "cost_usd": 0.0, "seconds": seconds}
        jev = getattr(client, "supports_decision_requests", False) is True  # Jev directly, or the gateway's classifier
        model = model or (self.model(role) if not jev else self.jev_model)
        for h in getattr(client, "history", []):
            if jev and h.get("model"):
                model = h["model"]  # the gateway says which backend model answered (jev-1.13.0)
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
_replaced = False  # set_ai was given an implementation (tests, the benchmark): it serves every workspace


def get_ai() -> AI:
    """The process-wide implementation, from the environment."""
    global _default
    with _default_lock:
        if _default is None:
            _default = Models()
        return _default


def set_ai(ai: AI | None) -> None:
    """Replace the process-wide implementation (None: back to the environment)."""
    global _default, _replaced
    with _default_lock:
        _default = ai
        _replaced = ai is not None


def replaced() -> bool:
    """Whether set_ai replaced the implementation, so workspace settings don't apply."""
    return _replaced


def configured(ai: AI | None = None, *roles: str) -> bool:
    """Whether every role (or the given ones) has a model."""
    ai = ai or get_ai()
    return all(ai.model(r) for r in (roles or ROLES))
