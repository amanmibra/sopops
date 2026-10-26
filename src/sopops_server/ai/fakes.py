"""Stand-ins for the models, for tests and offline runs. Nothing here reaches a network.

- `FakeModels`: an `AI` whose generative calls are answered by `responder(role, signature, inputs)` (the output
  fields, as a dict), and whose decision calls run through DSPy's real decision decoding against `decider`. A field
  the responder leaves out (or None) is answered by `default_reply` where it knows the signature (`SplitRule`,
  `NameRulePattern`), so a fake written for the checks still serves the programs that split rules first (calibration)
  and names patterns.
- `FakeSystemOne`: a System One client like `dspy.experimental.TypeSafe`: `responder(state, questions)` returns, per
  question, a probability (a Noul), or a label or a label -> probability mapping (a Choice).
- `FakeEngine`: a `dspy.LM` engine that answers in DSPy's chat format, for running decision signatures on a
  generative model: `responder(output_names, request_text)` returns the output fields.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

import dspy
from dspy.lm15 import Message, Response
from dspy.lm15 import Usage as LMUsage
from pydantic import BaseModel, TypeAdapter

from . import spend, tracing
from .lm import DECIDE, NotConfigured, Usage, as_predict
from .programs import rule_phrase
from .signatures import NameRulePattern, SplitRule

Responder = Callable[[str, type[dspy.Signature], dict[str, Any]], BaseModel | Mapping[str, Any]]
DecisionResponder = Callable[[dict[str, Any], dict[str, dict[str, Any]]], Mapping[str, Any]]


def split_rule(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """`SplitRule` without a model: the rule's own text is its one action (a warning sign's is its situation)."""
    rule = str(inputs.get("rule") or "").strip()
    if inputs.get("kind") == "warning":
        return {"checks": {"condition": rule, "actions": [], "forbidden": []}}
    return {"checks": {"condition": None, "actions": [rule], "forbidden": []}}


def name_rule_pattern(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """`NameRulePattern` without a model: the rule's text, trimmed."""
    return {"name": rule_phrase(str(inputs.get("rule") or ""))}


DEFAULT_REPLIES: dict[type[dspy.Signature], Callable[[Mapping[str, Any]], dict[str, Any]]] = {SplitRule: split_rule, NameRulePattern: name_rule_pattern}


def default_reply(signature: type[dspy.Signature], inputs: Mapping[str, Any]) -> dict[str, Any] | None:
    """A plain answer for a signature every fake needs the same way, or None."""
    make = DEFAULT_REPLIES.get(signature)
    return make(inputs) if make else None


class FakeSystemOne:
    supports_decision_requests = True
    cache = True  # answers are a function of the request, as a cached client's are (ReAnchor checks)

    def __init__(self, responder: DecisionResponder, model: str = "fake-jev"):
        self.responder = responder
        self.model = model
        self.requests: list[tuple[dict[str, Any], dict[str, dict[str, Any]]]] = []
        self.history: list[dict[str, Any]] = []

    def copy(self, **_: Any) -> FakeSystemOne:
        return self  # one request log across calls, for tests to read

    def __call__(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        self.requests.append((state, questions))
        got = self.responder(state, questions)
        out: dict[str, Any] = {}
        for name, q in questions.items():
            v = got[name]
            if q["type"] == "noul":
                out[name] = {"noul": float(v)}
            else:
                labels = list(q["criteria"])
                probs = {str(v): 1.0} if isinstance(v, str) else {str(k): float(p) for k, p in v.items()}
                full = {label: probs.get(label, 0.0) for label in labels}
                if not any(full.values()):
                    full[labels[0]] = 1.0
                out[name] = {"probabilities": full, "confidence": max(full.values())}
        tokens = len(json.dumps(state)) // 2 + len(json.dumps(questions)) // 2
        self.history.append({"usage": {"prompt_tokens": tokens, "completion_tokens": 0}})
        spend.report(self.model, {"prompt_tokens": tokens, "completion_tokens": 0})  # as Jev's client reports it to DSPy
        return out

    async def acall(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return self(state, questions)


class FakeModels:
    def __init__(self, responder: Responder | None = None, decider: FakeSystemOne | Any | None = None, models: Mapping[str, str] | None = None):
        self.responder = responder
        self.decider = decider
        self.models = dict(models) if models is not None else {"fast": "fake/fast", "reasoning": "fake/reasoning"}
        self.calls: list[tuple[str, type[dspy.Signature], dict[str, Any]]] = []
        self.headers: list[dict[str, str]] = []  # per call, the listeners' headers it would have sent to the model gateway
        self.usage = Usage()

    def model(self, role: str) -> str | None:
        if role == DECIDE:
            return getattr(self.decider, "model", None) if self.decider is not None else self.models.get("fast")
        return self.models.get(role)

    def decision_native(self) -> bool:
        return getattr(self.decider, "supports_decision_requests", False) is True

    def client(self, role: str) -> Any:
        """The decision client (what `Models.client("decide")` gives), for programs that run decisions themselves."""
        if role != DECIDE or self.decider is None:
            raise NotConfigured(f"no client for role '{role}'")
        return self.decider

    def predict(self, role: str, predictor: dspy.Predict | type[dspy.Signature], **inputs: Any) -> dspy.Prediction:
        p = as_predict(predictor)
        if not self.model(role):
            raise NotConfigured(f"no model for role '{role}'")
        trace = tracing.start(role, p.signature, inputs, self.model(role))
        self.headers.append(trace.gateway_headers(self.model(role)))  # what a gateway call would have sent
        try:
            got = self._answer(role, p, inputs)
        except BaseException as e:
            trace.failed(e)
            raise
        trace.done(got)
        return got

    def _answer(self, role: str, p: dspy.Predict, inputs: dict[str, Any]) -> dspy.Prediction:
        self.calls.append((role, p.signature, inputs))
        self.usage.add("jev" if role == DECIDE and self.decider is not None else role, {})
        if role == DECIDE and self.decider is not None:
            with dspy.context(lm=self.decider), spend.tracking():
                return p(**inputs)
        out = self.responder(role, p.signature, inputs) if self.responder is not None else None
        fields = out.model_dump() if isinstance(out, BaseModel) else dict(out or {})
        default = default_reply(p.signature, inputs)
        if default is not None:
            fields |= {n: v for n, v in default.items() if fields.get(n) is None}
        if self.responder is None and default is None:
            raise AssertionError(f"no responder for {p.signature.__name__}")
        with spend.tracking():  # what a model would have used: a token per four characters in and out
            spend.report(self.model(role), {"prompt_tokens": len(json.dumps(inputs, default=str)) // 4, "completion_tokens": len(json.dumps(fields, default=str)) // 4})
        return dspy.Prediction(**{n: TypeAdapter(f.annotation).validate_python(fields[n]) for n, f in p.signature.output_fields.items()})


class FakeEngine:
    """A `dspy.LM(..., engine=FakeEngine(...))` that writes the output fields in DSPy's chat format."""

    def __init__(self, responder: Callable[[list[str], str], Mapping[str, Any]]):
        self.responder = responder
        self.requests: list[Any] = []

    def complete(self, request: Any) -> Response:
        self.requests.append(request)
        text = "\n".join([request.system or ""] + [getattr(p, "text", "") for m in request.messages for p in m.parts])
        outputs = list(dict.fromkeys(re.findall(r"^\d+\. `(\w+)` \(", text.split("Your output fields are:", 1)[-1].split("All interactions", 1)[0], re.M)))
        got = self.responder(outputs, text)
        body = "".join(f"[[ ## {n} ## ]]\n{v if isinstance(v, str) else json.dumps(v)}\n\n" for n, v in got.items()) + "[[ ## completed ## ]]"
        return Response(id=None, model=request.model, message=Message.assistant(body), finish_reason="stop", usage=LMUsage(input_tokens=len(text) // 4, output_tokens=len(body) // 4))
