"""A workaround for DSPy 3.4.1's decision adapter on generative models.

`DecisionAdapter._prepare` turns each decision output into its evidence type with one
`Signature.with_updated_fields` per output. Each of those deep-copies every field and builds a new pydantic class, so
a request with n decision outputs costs O(n²) copying: about 2 s of CPU (holding the GIL) for a 40-question rule
check, on every call, cached or not. That made ReAnchor ~500× slower on a generative model than on Jev (whose
requests skip this path), and it slows every live check on the fast model.

The fix builds the request signature in one pass (one deep copy, one class) and keeps it, keyed by the signature's
shape and the questions; thresholds aren't part of the request, so a calibration's reruns reuse it. The result is the
same signature the original builds, so prompts and response caches are unchanged. Remove this once DSPy builds it once.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from copy import deepcopy
from typing import Any

import dspy
from dspy.adapters import decision as _decision
from dspy.dsp.utils.settings import settings

CACHE_SIZE = 512

_original_prepare = _decision.DecisionAdapter._prepare
_cache: OrderedDict[tuple[Any, ...], type[dspy.Signature]] = OrderedDict()
_lock = threading.Lock()


def _key(signature: type[dspy.Signature], questions: dict[str, Any]) -> tuple[Any, ...]:
    shape = tuple((name, repr(f.annotation), json.dumps(f.json_schema_extra, sort_keys=True, default=repr)) for name, f in signature.fields.items())
    return (signature.__name__, signature.instructions, shape, json.dumps(questions, sort_keys=True, ensure_ascii=False))


def request_signature(signature: type[dspy.Signature], types: dict[str, type], questions: dict[str, Any]) -> type[dspy.Signature]:
    """The signature `_prepare` sends: every decision output as its evidence type, described by its question."""
    key = _key(signature, questions)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    fields = deepcopy(signature.fields)
    for name, kind in types.items():
        fields[name].json_schema_extra = {**fields[name].json_schema_extra, "desc": "\n" + json.dumps(questions[name], ensure_ascii=False, indent=2)}
        fields[name].annotation = _decision.evidence_type(kind)
    built = dspy.Signature(fields, signature.instructions)
    with _lock:
        _cache[key] = built
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return built


def _prepare(self: Any, signature: type[dspy.Signature], demos: list[Any], inputs: dict[str, Any], lm_kwargs: dict[str, Any]) -> dict[str, Any]:
    if self.system_one or demos or settings.send_stream is not None:
        return _original_prepare(self, signature, demos, inputs, lm_kwargs)
    types = self.state.types
    questions = {name: self.state._question(name, signature.output_fields[name], kind) for name, kind in types.items()}
    return {"lm_kwargs": lm_kwargs, "signature": request_signature(signature, types, questions), "demos": [], "inputs": inputs}


def install() -> None:
    _decision.DecisionAdapter._prepare = _prepare


install()
