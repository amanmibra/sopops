"""Decisions through a model gateway's system-1 endpoint: one provider-neutral classifier name instead of a vendor's API.

A **system-1 classifier** answers a decision signature's questions (Noul / Choice / Score) with calibrated
probabilities, without generating text: TypeSafe's Jev is one. Where a deployment runs a model gateway that holds the
classifier keys (the hosted sopops does), sopops asks the gateway instead of calling the vendor itself:

    SOPOPS_SYSTEM1_URL    the gateway's system-1 base URL, e.g. http://127.0.0.1:4001/v1 (a bare host:port means
                          http://host:port/v1)
    SOPOPS_SYSTEM1_MODEL  the classifier name the gateway serves (default sopops-system1)
    SOPOPS_SYSTEM1_KEY    its key (default SOPOPS_LM_KEY, the model gateway's)
    SOPOPS_SYSTEM1=off    ignore the gateway's classifier

Without `SOPOPS_SYSTEM1_URL` nothing here runs, and decisions go where they always did (TypeSafe directly with
`TYPESAFE_API_KEY`, else the decision model). With both set, the gateway wins, except for a team's own TypeSafe key,
which still goes to TypeSafe directly (the team pays for it, and its key never leaves sopops).

**The wire.** `POST {url}/system1` with DSPy's System One request, which is what `dspy.experimental.TypeSafe` sends
too: `{"model", "state", "questions"}`. The gateway answers `{"model", "backend", "backend_model", "calibrated",
"answers", "usage"}`, with each answer as DSPy's decision adapter reads it (`noul`, or `probabilities` and
`confidence`). Thresholds, cuts and weights stay here, in DSPy's decoding, so calibrated thresholds mean exactly what
they meant on Jev directly. `calibrated: false` marks an answer from the gateway's LLM fallback, whose probabilities
are an LLM's own, as the decision model's are; it's decoded the same way, logged, never cached, and its spend is
recorded under the model that answered.

**Spend.** The call's usage goes to DSPy's usage tracker under `backend_model` (`jev-1.13.0`), so `ai.spend` prices
it as it priced Jev called directly.

**Unavailable.** A gateway that can't be reached, times out or answers 5xx / 429 / 401 / 403 / 404 raises
`Unavailable`; `lm.Models` then answers that decision with the decision model, as a deployment without a classifier
would. A 400 or 422 is a bad request, raised as is.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import dspy
import httpx
from dspy.dsp.utils.settings import settings

log = logging.getLogger("sopops_server.ai.system1")

URL_ENV, MODEL_ENV, KEY_ENV, FLAG_ENV = "SOPOPS_SYSTEM1_URL", "SOPOPS_SYSTEM1_MODEL", "SOPOPS_SYSTEM1_KEY", "SOPOPS_SYSTEM1"
DEFAULT_MODEL = "sopops-system1"
TIMEOUT_S = 120.0  # Jev answers long checks in tens of seconds; the gateway's own fallbacks add to that
ANSWER_KEYS = ("noul", "score", "choice", "confidence", "probabilities")
UNAVAILABLE_STATUS = {401, 403, 404, 408, 409, 429}


class Unavailable(RuntimeError):
    """The gateway's classifier gave no answer: the decision model should answer instead."""


@dataclass(frozen=True)
class Gateway:
    url: str  # the system-1 base URL, ending in /v1
    key: str
    model: str

    @property
    def endpoint(self) -> str:
        return f"{self.url}/system1"


def gateway(env: Mapping[str, str] | None = None) -> Gateway | None:
    """The gateway's system-1 classifier, when this deployment has one."""
    env = os.environ if env is None else env
    if (env.get(FLAG_ENV) or "").strip().lower() in ("off", "0", "false", "no"):
        return None
    url = (env.get(URL_ENV) or "").strip().rstrip("/")
    if not url:
        return None
    if "://" not in url:
        url = f"http://{url}/v1"
    key = (env.get(KEY_ENV) or "").strip() or (env.get("SOPOPS_LM_KEY") or "").strip()
    return Gateway(url, key, (env.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL)


def _check(questions: Mapping[str, Any], body: Any) -> dict[str, Any]:
    """The gateway's answer, or ValueError when it doesn't answer every question in a shape DSPy decodes."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ValueError("system-1 reply has no answers")
    answers = {}
    for name, q in questions.items():
        a = body["answers"].get(name)
        if not isinstance(a, dict):
            raise ValueError(f"system-1 reply has no answer for {name!r}")
        a = {k: a[k] for k in ANSWER_KEYS if k in a}
        if q.get("type") == "noul" and not isinstance(a.get("noul"), (int, float)):
            raise ValueError(f"system-1 answer for {name!r} has no probability")
        if q.get("type") in ("choice", "score") and not isinstance(a.get("probabilities"), dict):
            raise ValueError(f"system-1 answer for {name!r} has no probabilities")
        answers[name] = a
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return {
        "model": str(body.get("backend_model") or body.get("model") or ""),
        "backend": body.get("backend"),
        "calibrated": body.get("calibrated", True) is not False,
        "usage": {"prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                  "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)},
        "answers": answers,
    }


class GatewaySystemOne:
    """A DSPy System One client (what `dspy.experimental.TypeSafe` is) for the gateway's system-1 endpoint, so
    `dspy.Predict` sends it decision requests and decodes its answers exactly as it does Jev's."""

    supports_decision_requests = True

    def __init__(self, gw: Gateway, *, cache: bool = True, timeout: float = TIMEOUT_S, transport: httpx.BaseTransport | None = None,
                 async_transport: httpx.AsyncBaseTransport | None = None):
        self.gateway = gw
        self.model = gw.model
        self.cache = cache
        self.timeout = timeout
        self.transport = transport
        self.async_transport = async_transport
        self.history: list[dict[str, Any]] = []

    def copy(self, **kwargs: Any) -> GatewaySystemOne:
        return type(self)(self.gateway, cache=kwargs.get("cache", self.cache), timeout=self.timeout, transport=self.transport,
                          async_transport=self.async_transport)

    def _request(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        # The cache key: the classifier name and where it's served, never the key.
        return {"provider": "sopops-system1", "model": self.model, "base_url": self.gateway.url, "state": state, "questions": questions}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.gateway.key}"} if self.gateway.key else {}

    def _parse(self, questions: dict[str, Any], res: httpx.Response) -> dict[str, Any]:
        if res.status_code >= 500 or res.status_code in UNAVAILABLE_STATUS:
            raise Unavailable(f"{self.model}: the gateway answered {res.status_code}: {res.text[:300]}")
        if not res.is_success:
            raise ValueError(f"{self.model}: the gateway refused the request ({res.status_code}): {res.text[:300]}")
        try:
            return _check(questions, res.json())
        except (ValueError, TypeError) as e:
            raise Unavailable(f"{self.model}: unreadable answer: {e}") from e

    def _finish(self, request: dict[str, Any], response: dict[str, Any], cache_hit: bool) -> dict[str, Any]:
        if not response["calibrated"]:
            log.warning("%s answered by %s (%s) without calibrated confidence", self.model, response.get("backend"), response["model"])
        if self.cache and not cache_hit and response["calibrated"]:  # an LLM fallback's answer isn't kept
            dspy.cache.put(request, response)
        usage = {} if cache_hit else response["usage"]
        if settings.usage_tracker and usage:
            settings.usage_tracker.add_usage(response["model"] or self.model, usage)
        if not settings.disable_history:
            self.history.append({"request": {k: v for k, v in request.items() if k != "state"}, "response": copy.deepcopy(response),
                                 "usage": usage, "cache_hit": cache_hit, "model": response["model"] or self.model,
                                 "prompt": None, "messages": None, "outputs": [json.dumps(response["answers"])]})
            del self.history[: -settings.max_history_size]
        return copy.deepcopy(response["answers"])

    def __call__(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        request = self._request(state, questions)
        response = dspy.cache.get(request) if self.cache else None
        if response is None:
            try:
                with httpx.Client(timeout=self.timeout, transport=self.transport) as http:
                    res = http.post(self.gateway.endpoint, json={"model": self.model, "state": state, "questions": questions}, headers=self._headers())
            except httpx.HTTPError as e:
                raise Unavailable(f"{self.model}: {type(e).__name__}: {e}") from e
            return self._finish(request, self._parse(questions, res), False)
        return self._finish(request, response, True)

    async def acall(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        request = self._request(state, questions)
        response = dspy.cache.get(request) if self.cache else None
        if response is None:
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.async_transport) as http:
                    res = await http.post(self.gateway.endpoint, json={"model": self.model, "state": state, "questions": questions},
                                          headers=self._headers())
            except httpx.HTTPError as e:
                raise Unavailable(f"{self.model}: {type(e).__name__}: {e}") from e
            return self._finish(request, self._parse(questions, res), False)
        return self._finish(request, response, True)
