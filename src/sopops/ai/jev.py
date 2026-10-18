"""TypeSafe's Jev: a system-1 classifier that answers questions about a state with calibrated probabilities.

Jev is optional (`pip install 'sopops[jev]'`, the `typesafe-sdk` package). It's on when `SOPOPS_JEV=on`, or when
`TYPESAFE_API_KEY` is set and `SOPOPS_JEV` isn't `off`; the model is `SOPOPS_JEV_MODEL` (default `jev-1.13.0`, pinned
rather than `jev-latest` so thresholds tuned against it don't move). Without it, every step uses the LLM.

This module is the only place `typesafe_sdk` is imported, lazily, so sopops installs and runs without it. Questions
are plain `Noul` / `Choice` specs here and become SDK objects only in `TypeSafeJev`; tests use `FakeJev`.

Limits (docs.typesafe.ai, models): 32k tokens of state plus the longest question, 64k for a whole request; a Choice
takes at most 255 options. Price: $0.042 per million input tokens, output free.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "jev-1.13.0"
PRICE_PER_M_INPUT = {"jev-1.13.0": 0.042, "jev-1.14.0": 0.042}
TIMEOUT_S = 120.0  # the SDK's 10 s default is sized for small requests; a transcript with dozens of questions takes longer


class JevError(RuntimeError):
    """Jev couldn't be reached or rejected the request, after the SDK's retries."""


class TooLarge(JevError):
    """Jev rejected the request as too large (a 400 or 422)."""


@dataclass(frozen=True)
class Noul:
    """A yes/no question: `instructions` (text or a JSON object), and what counts as yes and as no."""

    instructions: str | dict[str, Any]
    true: str
    false: str


@dataclass(frozen=True)
class Choice:
    """Pick one of `options` (label -> description, or None for a bare label)."""

    instructions: str | dict[str, Any]
    options: dict[str, str | None]


Question = Noul | Choice


@dataclass
class Answers:
    """Every answer from a set of requests, by question key: a probability for a Noul, label -> probability for a Choice."""

    nouls: dict[str, float] = field(default_factory=dict)
    choices: dict[str, dict[str, float]] = field(default_factory=dict)
    model: str | None = None
    input_tokens: int = 0

    def update(self, other: Answers) -> None:
        self.nouls.update(other.nouls)
        self.choices.update(other.choices)
        self.model = self.model or other.model
        self.input_tokens += other.input_tokens

    def ranked(self, key: str) -> list[tuple[str, float]]:
        return sorted(self.choices.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)


@dataclass
class JevUsage:
    requests: int = 0
    input_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"requests": self.requests, "input_tokens": self.input_tokens, "cost_usd": round(self.cost_usd, 6), "seconds": round(self.seconds, 2)}


class Jev(Protocol):
    model: str
    usage: JevUsage

    def ask(self, state: dict[str, Any], questions: Mapping[str, Question]) -> Answers: ...


def tokens(value: Any) -> int:
    """A deliberately high token estimate from JSON length (short utterances wrapped in JSON run close to 2 chars a token)."""
    return int(len(json.dumps(value, ensure_ascii=False)) / 2.0) + 1


def question_tokens(q: Question) -> int:
    return tokens({"i": q.instructions, "t": q.true, "f": q.false} if isinstance(q, Noul) else {"i": q.instructions, "o": q.options})


class _Usage:
    def __init__(self, model: str):
        self.model = model
        self.usage = JevUsage()
        self._lock = threading.Lock()

    def _record(self, input_tokens: int, seconds: float) -> None:
        with self._lock:
            self.usage.requests += 1
            self.usage.input_tokens += input_tokens
            self.usage.cost_usd += input_tokens * PRICE_PER_M_INPUT.get(self.model, 0.042) / 1e6
            self.usage.seconds += seconds


class TypeSafeJev(_Usage):
    """The real client, over `typesafe-sdk`. The key comes from `TYPESAFE_API_KEY`."""

    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = TIMEOUT_S):
        super().__init__(model)
        try:
            import typesafe_sdk as sdk  # optional dependency: `pip install 'sopops[jev]'`
        except ImportError as e:
            raise JevError("Jev needs the typesafe-sdk package: pip install 'sopops[jev]'") from e
        self._sdk = sdk
        # The SDK retries 408, 429, 5xx and timeouts (a system_one request is a read); the budget covers a full slow
        # attempt plus the retries.
        self._client = sdk.TypeSafeClient(model=model, timeout=timeout, retry=sdk.RetryPolicy(max_retries=3, timeout=4 * timeout))

    def ask(self, state: dict[str, Any], questions: Mapping[str, Question]) -> Answers:
        sdk = self._sdk
        qs = {
            k: sdk.Noul(instructions=q.instructions, criteria=sdk.NoulCriteria(true=q.true, false=q.false))
            if isinstance(q, Noul)
            else sdk.Choice(instructions=q.instructions, criteria=dict(q.options))
            for k, q in questions.items()
        }
        started = time.monotonic()
        try:
            r = self._client.system_one(state, qs, model=self.model)
        except (sdk.TypeSafeBadRequestError, sdk.TypeSafeUnprocessableEntityError) as e:
            raise TooLarge(f"{self.model}: {type(e).__name__}: {str(e)[:300]}") from e
        except sdk.TypeSafeError as e:
            raise JevError(f"{self.model}: {type(e).__name__}: {str(e)[:300]}") from e
        n = int(r.usage.input_tokens or 0)
        self._record(n, time.monotonic() - started)
        return Answers(
            nouls={k: float(a.noul) for k, a in r.nouls.items()},
            choices={k: dict(a.probabilities) for k, a in r.choices.items()},
            model=r.model,
            input_tokens=n,
        )


Responder = Callable[[dict[str, Any], Mapping[str, Question]], Mapping[str, float | str | dict[str, float]]]


class FakeJev(_Usage):
    """For tests: `responder(state, questions)` returns, per question key, a probability (Noul) or a label or a
    label -> probability mapping (Choice). Every request is kept in `requests`."""

    def __init__(self, responder: Responder, model: str = "fake-jev"):
        super().__init__(model)
        self.responder = responder
        self.requests: list[tuple[dict[str, Any], dict[str, Question]]] = []

    def ask(self, state: dict[str, Any], questions: Mapping[str, Question]) -> Answers:
        self.requests.append((state, dict(questions)))
        got = self.responder(state, questions)
        out = Answers(model=self.model, input_tokens=tokens(state) + sum(map(question_tokens, questions.values())))
        for k, q in questions.items():
            v = got[k]
            if isinstance(q, Noul):
                out.nouls[k] = float(v)  # type: ignore[arg-type]
            else:
                out.choices[k] = {str(v): 1.0} if isinstance(v, str) else dict(v)  # type: ignore[arg-type]
        self._record(out.input_tokens, 0.0)
        return out


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    flag = (env.get("SOPOPS_JEV") or "").strip().lower()
    if flag in ("off", "0", "false", "no"):
        return False
    return flag in ("on", "1", "true", "yes") or bool((env.get("TYPESAFE_API_KEY") or "").strip())


_default: Jev | None = None
_resolved = False
_lock = threading.Lock()


def get_jev() -> Jev | None:
    """The process-wide Jev client, or None when it's off."""
    global _default, _resolved
    with _lock:
        if not _resolved:
            _default = TypeSafeJev(os.environ.get("SOPOPS_JEV_MODEL") or DEFAULT_MODEL) if enabled() else None
            _resolved = True
        return _default


def set_jev(jev: Jev | None) -> None:
    """Replace the process-wide client (None: off). `reset_jev()` goes back to the environment."""
    global _default, _resolved
    with _lock:
        _default, _resolved = jev, True


def reset_jev() -> None:
    global _default, _resolved
    with _lock:
        _default, _resolved = None, False
