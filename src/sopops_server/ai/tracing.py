"""Model-call listeners, for tracing and observability. An extension (`sopops_server.extensions`) registers one with
`add_listener(after, before=None)`; sopops registers none itself, and with none registered nothing is collected.

Every model call made through `AI.predict` (`Models` and the fakes) is seen, generative calls and decisions alike
(Jev called directly, a gateway's system-1 classifier, or the decision model; `ModelCall.decision`):

- `before(call)` runs as the call starts and may return extra request headers for it (a trace id, say). They're
  added only when the call goes to the hosted model gateway (a `sopops/...` model, `sopops_server.ai.lm`); a team's
  own keys and a self-hosted model never get them.
- `after(call, result)` runs once it's done, with what came back or the error.

`ModelCall` says what the call is: the workspace, session and purpose in scope (`sopops_server.ai.spend`), the
signature, its structured inputs, the model, the instructions' version (`signatures.VERSIONS`), whether it's a
decision, and `context`: what the caller said it was about (`procedure_id`; `rules`, a reply's rule keys to rule ids,
for `CheckRules` and `LocateEvidence`). `check_run_id` is shared by every call of one session's check (routing and
each procedure's check of one revision, `run_id`), so a listener can group them into one trace. `ModelResult` has the
outputs (and the model's reasoning, apart, when the reply has one), the tokens the provider reported (none on a
cache hit), the latency and the error, if it failed.

A listener runs in the caller's thread. One that raises is logged and skipped, so tracing never breaks a check.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import dspy
from pydantic import BaseModel

from . import spend
from .signatures import VERSIONS

log = logging.getLogger("sopops_server.ai.tracing")

# The instructions' version of each generative signature; decision signatures are built per call, as `decide`.
SIGNATURE_VERSIONS = {"CheckRules": "check", "LocateEvidence": "locate", "SplitRule": "split", "NameRulePattern": "name", "ClusterFindings": "cluster",
                      "GroupThemes": "themes", "SuggestEdit": "suggest", "SegmentPrompt": "segment", "StructureProcedure": "procedure", "NameVariables": "variables"}
GATEWAY_PREFIX = "sopops/"
_RUN_NAMESPACE = uuid.UUID("5d1b7a52-3c55-4f0e-9d0a-6a6f2b8f1c11")


@dataclass(frozen=True)
class ModelCall:
    """One model call, as it starts."""

    call_id: str
    workspace: str | None
    session_id: str | None
    purpose: str | None
    signature: str
    role: str
    model: str | None
    prompt_version: str | None
    inputs: dict[str, Any]
    decision: bool = False
    check_run_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def procedure_id(self) -> str | None:
        return self.context.get("procedure_id")

    @property
    def rule_ids(self) -> list[str]:
        return [r for r in (self.context.get("rules") or {}).values() if r]


@dataclass(frozen=True)
class ModelResult:
    """How it went: the outputs (empty on an error), tokens, latency."""

    outputs: dict[str, Any]
    reasoning: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None


Before = Callable[[ModelCall], Mapping[str, str] | None]
After = Callable[[ModelCall, ModelResult], None]


@dataclass(frozen=True)
class Listener:
    after: After
    before: Before | None = None


_listeners: list[Listener] = []
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("sopops_trace_context", default={})


def add_listener(after: After, before: Before | None = None) -> Listener:
    """Register a listener; returns it, for `remove_listener`."""
    lst = Listener(after, before)
    _listeners.append(lst)
    return lst


def remove_listener(listener: Listener | After) -> None:
    """Unregister a listener (or every one with this `after`)."""
    _listeners[:] = [x for x in _listeners if x is not listener and x.after is not listener]


def listening() -> bool:
    return bool(_listeners)


def run_id(session_id: str, revision: str) -> str:
    """The check-run id of one revision of a session: the same in every process that checks it."""
    return str(uuid.uuid5(_RUN_NAMESPACE, f"{session_id}:{revision}"))


@contextmanager
def context(**fields: Any) -> Iterator[None]:
    """The calls inside it carry these fields (`check_run_id` on the call, the rest in `ModelCall.context`), over any
    the enclosing context set."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def plain(value: Any) -> Any:
    """A value as JSON-ready data: Pydantic models dumped, containers walked, anything else unknown as text."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):  # a decision output (a Noul or a Choice)
        return {"value": plain(getattr(value, "value")), "probability": plain(getattr(value, "probability", None))}
    return str(value)


class Trace:
    """One call in flight: `headers` from the `before` callbacks, then `done` or `failed`."""

    def __init__(self, call: ModelCall | None):
        self.call = call
        self.headers: dict[str, str] = {}
        self.started = time.monotonic()
        self.acc, self._token = spend.count_tokens() if call is not None else ([0, 0], None)
        if call is None:
            return
        for lst in list(_listeners):
            if lst.before is None:
                continue
            try:
                self.headers.update({str(k): str(v) for k, v in (lst.before(call) or {}).items()})
            except Exception:
                log.exception("model-call listener %r failed before a call", lst.before)

    def gateway_headers(self, model: str | None) -> dict[str, str]:
        """The headers to send, for a call that goes to the hosted model gateway (none otherwise)."""
        return dict(self.headers) if model and model.startswith(GATEWAY_PREFIX) else {}

    def done(self, prediction: dspy.Prediction) -> None:
        if self.call is None:
            return
        try:
            outputs = plain(dict(prediction.items()) if hasattr(prediction, "items") else {})
            reasoning = outputs.pop("reasoning", None) if isinstance(outputs, dict) else None
        except Exception:
            log.exception("couldn't trace a model call's outputs")
            outputs, reasoning = {}, None
        self._finish(ModelResult(outputs, str(reasoning) if reasoning is not None else None, self.acc[0], self.acc[1], self._ms()))

    def failed(self, error: BaseException) -> None:
        if self.call is None:
            return
        self._finish(ModelResult({}, None, self.acc[0], self.acc[1], self._ms(), f"{type(error).__name__}: {str(error)[:1000]}"))

    def _ms(self) -> float:
        return round((time.monotonic() - self.started) * 1000, 1)

    def _finish(self, result: ModelResult) -> None:
        if self._token is not None:
            spend.stop_counting(self._token)
            self._token = None
        for lst in list(_listeners):
            try:
                lst.after(self.call, result)  # type: ignore[arg-type]
            except Exception:  # tracing is bookkeeping: never fail the work over it
                log.exception("model-call listener %r failed", lst.after)


def start(role: str, signature: type[dspy.Signature], inputs: Mapping[str, Any], model: str | None) -> Trace:
    """A call starting: runs the `before` callbacks (nothing to do without a listener)."""
    if not _listeners:
        return Trace(None)
    try:
        name = signature.__name__
        ctx = dict(_context.get())
        run = ctx.pop("check_run_id", None)
        decision = role == "decide"
        version = ctx.pop("prompt_version", None) or VERSIONS.get(SIGNATURE_VERSIONS.get(name, ""), None) or (VERSIONS["decide"] if decision else None)
        scope = spend.current()
        call = ModelCall(call_id=str(uuid.uuid4()), workspace=scope.workspace if scope else None, session_id=scope.session_id if scope else None,
                         purpose=scope.purpose if scope else None, signature=name, role=role, model=model, prompt_version=version,
                         inputs=plain(dict(inputs)), decision=decision, check_run_id=run, context=plain(ctx))
    except Exception:
        log.exception("couldn't trace a model call")
        return Trace(None)
    return Trace(call)
