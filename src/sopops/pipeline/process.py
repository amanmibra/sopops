"""`process_session`: what runs after a session is ingested.

1. normalize: canonical turns and tool calls; status received (or partial, or failed).
2. route (fast model): which procedures applied, and which are excluded because the session ended before the
   procedure started or finished. Status routed, or excluded when there's nothing to check.
3. check (reasoning model, one call per applicable procedure): every rule's status, the goal, and the rollup.
4. finish: status checked (a partial session stays partial), with the worst procedure result.
5. patterns: each new finding joins its rule's pattern (the fast model picks a failure mode on split rules), then
   rules and themes that are due are reclustered and regrouped (see `sopops.patterns`). A failure here never fails the
   session; the findings stay unassigned until the next run or the daily recluster.

Each step is a plain function, so it runs the same inline and as a Hatchet task (`sopops.jobs.hatchet`). With no
model configured the session stays `received`, with the reason, instead of failing.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .. import llm as llm_mod
from ..ai import classify
from ..ai import jev as jev_mod
from ..ai import lm as ai_lm
from ..errors import NotFound
from . import judge, prompts
from .normalize import normalize_tool_calls, normalize_turns
from .procedures import Procedure, candidates
from .rollup import apply_tool_calls, mark_out_of_order, rollup
from .types import CheckRef, CheckResult, ProcessResult, RouteResult, SessionRef

if TYPE_CHECKING:
    from ..llm import LLM
    from ..orm import SessionRecord
    from ..patterns import Outcome
    from ..store import Store

log = logging.getLogger("sopops.pipeline")
NO_MODEL = "No model configured: set SOPOPS_MODEL_FAST and SOPOPS_MODEL_REASONING to check sessions against procedures."
SENDER_PARTIAL = "The sender marked the transcript incomplete."


def process_session(store: Store, ref: SessionRef, llm: LLM | None = None) -> ProcessResult:
    """Every step, in this process."""
    n = normalize_session(store, ref)
    if n.skipped or n.status == "failed" or n.turn_count == 0:
        return n
    r = route_session(store, ref, llm)
    if not r.go:
        return ProcessResult(session_id=ref.session_id, status=r.status, turn_count=n.turn_count, skipped=r.status == "stale", reason=r.reason)
    for pid in r.to_check:
        c = check_procedure(store, CheckRef(**ref.model_dump(), procedure_id=pid), llm)
        if not c.stored:
            return ProcessResult(session_id=ref.session_id, status=n.status, turn_count=n.turn_count, skipped=True)
    done = finish_session(store, ref, n.turn_count)
    if not done.skipped:
        assign_patterns(store, ref, llm)
    return done


def normalize_session(store: Store, ref: SessionRef) -> ProcessResult:
    try:
        session = store.sessions.load(ref.workspace_id, ref.session_id)
    except NotFound:  # deleted since: nothing to do, and nothing to retry
        return ProcessResult(session_id=ref.session_id, status="failed", turn_count=0, skipped=True)
    if session.payload_hash != ref.revision:
        return ProcessResult(session_id=ref.session_id, status=session.status, turn_count=len(session.turns), skipped=True)
    try:
        turns = normalize_turns(session.turns, session.started_at)
        tool_calls = normalize_tool_calls(session.tool_calls, session.started_at)
    except Exception as e:  # a payload we can't make sense of: keep it, and say why
        store.sessions.finish(ref, session.turns, session.tool_calls, "failed", f"normalize: {e}")
        raise
    if session.status_reason == SENDER_PARTIAL:  # set at ingest when the sender says the transcript is incomplete
        status, reason = "partial", session.status_reason
    elif not turns:
        status, reason = "partial", "No turns."
    else:
        status, reason = "received", None
    applied = store.sessions.finish(ref, turns, tool_calls, status, reason)
    return ProcessResult(session_id=ref.session_id, status=status, turn_count=len(turns), skipped=not applied)


def _load(store: Store, ref: SessionRef) -> SessionRecord | None:
    try:
        s = store.sessions.load(ref.workspace_id, ref.session_id)
    except NotFound:
        return None
    return s if s.payload_hash == ref.revision else None


def _partial(s: SessionRecord) -> bool:
    """The sender said the transcript is incomplete: the session is still checked, but its status stays partial."""
    return s.status_reason == SENDER_PARTIAL


def route_session(store: Store, ref: SessionRef, llm: LLM | None = None) -> RouteResult:
    llm = llm or llm_mod.get_llm()
    s = _load(store, ref)
    if s is None:
        return RouteResult(session_id=ref.session_id, go=False, status="stale")
    if not s.turns:
        return RouteResult(session_id=ref.session_id, go=False, status=s.status, reason=s.status_reason)
    partial = _partial(s)
    base = "partial" if partial else "received"
    if not llm_mod.configured(llm):
        store.checks.reset(ref, base, SENDER_PARTIAL if partial else NO_MODEL)
        return RouteResult(session_id=ref.session_id, go=False, status=base, reason=NO_MODEL)
    cand = candidates(store, s)
    if cand.release is None or not cand.procedures:
        why = "No release to check against." if cand.release is None else f"Release {cand.release} has no procedures for this agent."
        status = "partial" if partial else (base if cand.release is None else "excluded")
        store.checks.reset(ref, status, SENDER_PARTIAL if partial else why)
        return RouteResult(session_id=ref.session_id, go=False, status=status, release_id=cand.release, reason=why)
    jev = jev_mod.get_jev()
    model, version = llm.model("fast"), prompts.load("route").version
    try:
        if jev is None:
            decisions = judge.route(llm, cand.procedures, cand.variables, s.turns, s.tool_calls)
        else:
            decisions, by_llm = classify.route(
                jev, cand.procedures, cand.variables, s.turns, s.tool_calls, classify.JevConfig.from_env(),
                fallback=lambda: judge.route(llm, cand.procedures, cand.variables, s.turns, s.tool_calls),
            )
            if not by_llm:
                model, version = jev.model, classify.ROUTE_VERSION
    except Exception as e:
        store.checks.fail(ref, f"route: {e}")
        raise
    to_check = [d.procedure_id for d in decisions if d.status == "applied"]
    if to_check:
        status, reason = "routed", None
    else:
        excluded = [d for d in decisions if d.excluded_reason]
        status, reason = "excluded", (excluded[0].excluded_reason if excluded else "No procedure applied.")
    if partial:
        status, reason = "partial", SENDER_PARTIAL
    stored = store.checks.routed(ref, cand.procedures, decisions, status, reason, model, version)
    if not stored:
        return RouteResult(session_id=ref.session_id, go=False, status="stale")
    return RouteResult(session_id=ref.session_id, go=bool(to_check), status=status, release_id=cand.release, to_check=to_check, reason=reason)


def check_procedure(store: Store, ref: CheckRef, llm: LLM | None = None) -> CheckResult:
    llm = llm or llm_mod.get_llm()
    s = _load(store, ref)
    if s is None:
        return CheckResult(session_id=ref.session_id, procedure_id=ref.procedure_id, result=None, stored=False)
    cand = candidates(store, s)
    p: Procedure | None = next((x for x in cand.procedures if x.id == ref.procedure_id), None)
    if p is None:
        return CheckResult(session_id=ref.session_id, procedure_id=ref.procedure_id, result=None, stored=False)
    jev = jev_mod.get_jev()
    model, version = llm.model("reasoning"), prompts.load("check").version
    try:
        if jev is None:
            checked = judge.check(llm, p, cand.variables, s.turns, s.tool_calls)
        else:
            got = classify.check(jev, ai_lm.get_ai(), store.engine, ref.workspace_id, p, cand.variables, s.turns, s.tool_calls, classify.JevConfig.from_env())
            checked, model, version = got.checked, got.model, got.prompt_version
    except Exception as e:
        store.checks.fail(ref, f"check {p.id}: {e}")
        raise
    steps = [r for r in checked.rules if r.kind == "step"]
    tools = {r.id: r.tool for r in p.rules if r.kind == "step" and r.tool and r.required}
    apply_tool_calls(steps, tools, s.tool_calls, s.turns)
    mark_out_of_order(steps)
    agent_spoke = any(t.get("role") == "agent" and t.get("text") for t in s.turns)
    roll = rollup(checked.rules, checked.goal_reached, agent_spoke)
    stored = store.checks.save(ref, p, checked.rules, checked.goal_reached, checked.goal_reason, roll, model, version)
    return CheckResult(session_id=ref.session_id, procedure_id=p.id, result=roll.result, stored=stored)


def finish_session(store: Store, ref: SessionRef, turn_count: int | None = None) -> ProcessResult:
    s = _load(store, ref)
    if s is None:
        return ProcessResult(session_id=ref.session_id, status="failed", turn_count=0, skipped=True)
    done = store.checks.finish(ref, keep_partial=_partial(s))
    if done is None:
        return ProcessResult(session_id=ref.session_id, status=s.status, turn_count=len(s.turns), skipped=True)
    status, result = done
    return ProcessResult(session_id=ref.session_id, status=status, turn_count=turn_count if turn_count is not None else len(s.turns), result=result)


_ws_locks: dict[str, threading.Lock] = {}
_ws_locks_guard = threading.Lock()


def _ws_lock(workspace: str) -> threading.Lock:
    with _ws_locks_guard:
        return _ws_locks.setdefault(workspace, threading.Lock())


def assign_patterns(store: Store, ref: SessionRef, llm: LLM | None = None, recluster: bool = True) -> Outcome | None:
    """Step 5, in this process: one workspace's pattern writes at a time (Hatchet runs them with concurrency 1 per
    workspace instead). Returns None if it failed (logged)."""
    try:
        with _ws_lock(ref.workspace_id):
            return store.patterns.assign_session(ref.workspace_id, ref.session_id, llm, recluster=recluster)
    except Exception:
        log.exception("assigning patterns failed for session %s", ref.session_id)
        return None
