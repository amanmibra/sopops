"""Model spend: one `model_calls` row per model call, with its tokens and cost, for the workspace, the session it was
about and what it was for. Prompts and replies are never stored.

**Where calls are seen.** Every client DSPy runs (a `dspy.LM`, on its native or LiteLLM path, and Jev) reports a
call's normalized usage to `dspy.settings.usage_tracker` (cache hits report nothing: they cost nothing). sopops puts
its `Tracker` there for every model call it makes (`Models.predict`, and `scope` for programs that run a predictor on
a client themselves, like calibration), so nothing is missed whichever path a call takes. The fakes report the same way.

**Attribution.** `scope(engine, workspace, purpose, session_id)` says what the calls inside it are for; `purpose`
renames it for a nested part (the reviewer's pass inside a check). Purposes: `route`, `check`, `review`, `cluster`
(assigning findings, splitting and naming patterns), `theme`, `suggestion`, `calibrate`, `onboarding`. A call outside
any scope isn't recorded (there's no workspace to give it). Scopes are context variables: `carry` runs a function in
a worker thread with the caller's scope. Rows are written when the scope ends, in one transaction of their own, so a
call made inside a caller's open transaction never waits on it (SQLite has one writer).

**Cost** is an estimate from `PRICES` (USD per million input and output tokens, first-party list prices), which
`SOPOPS_MODEL_PRICES` (JSON, `{"model": [input, output]}`) extends or overrides. A model string matches its name
without the provider (`anthropic/claude-haiku-4-5` is `claude-haiku-4-5`), else the longest entry it starts with (a
dated snapshot, `jev-1.13.0`). A model with no price records its tokens and a null cost.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, TypeVar

import dspy
from sqlalchemy.engine import Engine
from sqlmodel import Session

from ..orm import ModelCall

log = logging.getLogger("sopops.ai.spend")

PURPOSES = ("route", "check", "review", "cluster", "theme", "suggestion", "calibrate", "onboarding")
PRICES_ENV = "SOPOPS_MODEL_PRICES"
# USD per million tokens (input, output). Jev: docs.typesafe.ai/models, output free.
PRICES: dict[str, tuple[float, float]] = {
    "jev": (0.042, 0.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
}

T = TypeVar("T")


@dataclass(frozen=True)
class Scope:
    engine: Engine
    workspace: str
    purpose: str
    session_id: str | None = None
    rows: list[ModelCall] = field(default_factory=list, compare=False)  # written when the scope ends (shared by `purpose`)


_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("sopops_spend_scope", default=None)


# --- prices ----------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _prices(env_value: str) -> dict[str, tuple[float, float, str]]:
    out = {k: (i, o, "default") for k, (i, o) in PRICES.items()}
    if env_value.strip():
        try:
            for k, v in json.loads(env_value).items():
                i, o = (v["input"], v["output"]) if isinstance(v, Mapping) else v
                out[str(k).lower()] = (float(i), float(o), "env")
        except (ValueError, TypeError, KeyError) as e:
            log.warning("%s isn't JSON like {\"model\": [input, output]}: %s; using the defaults", PRICES_ENV, e)
    return out


def prices(env: Mapping[str, str] | None = None) -> dict[str, tuple[float, float, str]]:
    """{model: (input $/M, output $/M, source)}, source `default` or `env`."""
    env = os.environ if env is None else env
    return _prices(env.get(PRICES_ENV) or "")


def price_of(model: str | None, env: Mapping[str, str] | None = None) -> tuple[float, float] | None:
    """(input $/M, output $/M) for a model string, or None when it has no price."""
    if not model:
        return None
    table = prices(env)
    full = model.strip().lower()
    for name in (full, full.rsplit("/", 1)[-1]):
        if name in table:
            return table[name][:2]
    name = full.rsplit("/", 1)[-1]
    best = max((k for k in table if name.startswith(k)), key=len, default=None)
    return table[best][:2] if best else None


def cost(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    p = price_of(model)
    return None if p is None else (input_tokens * p[0] + output_tokens * p[1]) / 1e6


# --- attribution -------------------------------------------------------------------------------------------------------


@contextmanager
def scope(engine: Engine, workspace: str, purpose: str, session_id: str | None = None) -> Iterator[Scope]:
    """Record the model calls inside it for `workspace` (and `session_id`), as `purpose`."""
    s = Scope(engine, workspace, purpose, session_id)
    token = _scope.set(s)
    try:
        with tracking():
            yield s
    finally:
        _scope.reset(token)
        _write(s)


@contextmanager
def purpose(name: str) -> Iterator[None]:
    """The calls inside it are for `name` instead (in the current scope, if there is one)."""
    cur = _scope.get()
    token = _scope.set(replace(cur, purpose=name)) if cur is not None else None
    try:
        yield
    finally:
        if token is not None:
            _scope.reset(token)


def current() -> Scope | None:
    return _scope.get()


def carry(fn: Callable[..., T]) -> Callable[..., T]:
    """`fn` to run in worker threads (`pool.map(carry(fn), items)`) with the caller's scope (and DSPy settings)."""
    ctx = contextvars.copy_context()

    def run(*args: Any, **kwargs: Any) -> T:
        return ctx.copy().run(fn, *args, **kwargs)

    return run


# --- recording ---------------------------------------------------------------------------------------------------------


def _tokens(usage: Mapping[str, Any]) -> tuple[int, int]:
    def n(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return 0

    return n("prompt_tokens", "input_tokens"), n("completion_tokens", "output_tokens")


def record(model: str | None, usage: Mapping[str, Any] | Any) -> None:
    """One call's usage (DSPy's normalized dict, or an object with those attributes), in the current scope."""
    s = _scope.get()
    if s is None or not model:
        return
    if not isinstance(usage, Mapping):
        usage = {k: getattr(usage, k, None) for k in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")}
    i, o = _tokens(usage)
    s.rows.append(ModelCall(workspace_id=s.workspace, session_id=s.session_id, purpose=s.purpose, model=str(model)[:256], input_tokens=i,
                            output_tokens=o, cost_usd=cost(model, i, o), created_at=datetime.now(timezone.utc)))


def _write(s: Scope) -> None:
    rows, s.rows[:] = list(s.rows), []
    if not rows:
        return
    try:
        with Session(s.engine) as db:
            db.add_all(rows)
            db.commit()
    except Exception:  # spend is bookkeeping: never fail the work over it
        log.exception("recording %d model calls for %s failed", len(rows), s.workspace)


class Tracker:
    """DSPy's usage tracker interface (`add_usage(model, usage)`), recording into `model_calls`, and passing the usage on
    to a tracker that was already there (`dspy.track_usage`, a benchmark)."""

    def __init__(self, then: Any = None):
        self.then = then

    def add_usage(self, model: str, usage: Mapping[str, Any]) -> None:
        record(model, usage or {})
        if self.then is not None:
            self.then.add_usage(model, usage)


@contextmanager
def tracking() -> Iterator[None]:
    """DSPy reports every call inside it to `Tracker` (once, however many are nested)."""
    have = dspy.settings.usage_tracker
    if isinstance(have, Tracker):
        yield
        return
    with dspy.context(usage_tracker=Tracker(have)):
        yield


def report(model: str | None, usage: Mapping[str, Any]) -> None:
    """For clients DSPy doesn't account (the fakes): send a call's usage where DSPy would."""
    t = dspy.settings.usage_tracker
    if t is not None and model:
        t.add_usage(model, dict(usage))
