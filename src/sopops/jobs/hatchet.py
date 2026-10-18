"""All Hatchet wiring. Workflows wrap the plain functions in `sopops.pipeline`; nothing else imports hatchet_sdk.

Needs `pip install 'sopops[hatchet]'` and HATCHET_CLIENT_TOKEN (plus HATCHET_CLIENT_TLS_STRATEGY=none for a local
hatchet-lite). The worker (`sopops worker`) runs the tasks; the API only queues runs.

`process_session` is a DAG: normalize -> route -> check -> (done). Model calls run in child workflows so each one can
be rate-limited by its model key (`SOPOPS_RATE_LIMIT_PER_MINUTE` calls per minute per model, default 60):
`sopops_route` for routing, and one `sopops_check` per applicable procedure, fanned out by the `check` task, which
then rolls the session up. The `patterns` task then assigns the session's findings to patterns; pattern writes run one
at a time per workspace (`sopops_patterns`), and so do reclustering and themes (`sopops_recluster`, queued when due),
plus a daily cron (`sopops_recluster_daily`, SOPOPS_RECLUSTER_CRON, default 06:00 UTC).
"""

from __future__ import annotations

import os
from datetime import timedelta

from hatchet_sdk import (
    ConcurrencyExpression,
    ConcurrencyLimitStrategy,
    Context,
    Hatchet,
    IdempotencyCollisionError,
    StatusBasedIdempotencyConfig,
)
from hatchet_sdk.rate_limit import RateLimit, RateLimitDuration
from sqlmodel import Session, select

from ..ai import lm as ai_lm
from ..db import database_url
from ..orm import Finding
from ..pipeline.process import (
    check_procedure,
    finish_session,
    normalize_session,
    route_session,
)
from ..pipeline.types import (
    CheckRef,
    CheckResult,
    ProcessResult,
    ReclusterRef,
    RouteRef,
    RouteResult,
    SessionRef,
)
from ..sopc import Sopc
from ..store import Store

hatchet = Hatchet()
RATE = int(os.environ.get("SOPOPS_RATE_LIMIT_PER_MINUTE", "60"))
MODEL_LIMIT = RateLimit(dynamic_key="input.model_key", units=1, limit="input.rate_limit", duration=RateLimitDuration.MINUTE)

# One run per session revision (a re-sent session with new content gets a new run), and workspaces share the
# workers fairly: at most SOPOPS_WORKSPACE_CONCURRENCY runs per workspace at once, round robin across workspaces.
process_session_wf = hatchet.workflow(
    name="process_session",
    input_validator=SessionRef,
    concurrency=ConcurrencyExpression(
        expression="input.workspace_id",
        max_runs=int(os.environ.get("SOPOPS_WORKSPACE_CONCURRENCY", "4")),
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
    idempotency=StatusBasedIdempotencyConfig(key_expression="input.session_id + ':' + input.revision", fallback_ttl=timedelta(days=1)),
)
route_wf = hatchet.workflow(name="sopops_route", input_validator=RouteRef)
check_wf = hatchet.workflow(name="sopops_check", input_validator=CheckRef)
# pattern writes for a workspace never race: one assign or recluster run at a time per workspace
_per_workspace = ConcurrencyExpression(expression="input.workspace_id", max_runs=1, limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN)
patterns_wf = hatchet.workflow(name="sopops_patterns", input_validator=SessionRef, concurrency=_per_workspace)
recluster_wf = hatchet.workflow(name="sopops_recluster", input_validator=ReclusterRef, concurrency=_per_workspace)
recluster_daily_wf = hatchet.workflow(name="sopops_recluster_daily", on_crons=[os.environ.get("SOPOPS_RECLUSTER_CRON", "0 6 * * *")])

_store = None


def _get_store():
    """The worker's store, from DATABASE_URL. The API process runs migrations; the worker doesn't."""
    global _store
    if _store is None:
        _store = Store(database_url(), Sopc(), migrate=False)
    return _store


def _model_key(role: str) -> str:
    return ai_lm.get_ai().model(role) or "none"


@process_session_wf.task(execution_timeout=timedelta(minutes=2), retries=3, backoff_factor=2.0, backoff_max_seconds=30)
def normalize(input: SessionRef, ctx: Context) -> ProcessResult:
    return normalize_session(_get_store(), input)


@process_session_wf.task(parents=[normalize], execution_timeout=timedelta(minutes=10), retries=1)
def route(input: SessionRef, ctx: Context) -> RouteResult:
    n = ctx.task_output(normalize)
    if n.skipped or n.status == "failed" or n.turn_count == 0:
        return RouteResult(session_id=input.session_id, go=False, status=n.status)
    ref = RouteRef(**input.model_dump(), model_key=_model_key("decide"), rate_limit=RATE)
    out = route_wf.run(ref, child_key=f"route:{input.session_id}:{input.revision}")
    return RouteResult.model_validate(out["route_one"])


@process_session_wf.task(parents=[route], execution_timeout=timedelta(minutes=30), retries=1)
def check(input: SessionRef, ctx: Context) -> ProcessResult:
    r = ctx.task_output(route)
    if not r.go:
        return ProcessResult(session_id=input.session_id, status=r.status, turn_count=0, skipped=r.status == "stale", reason=r.reason)
    model_key = _model_key("reasoning")
    runs = [
        check_wf.create_bulk_run_item(
            CheckRef(**input.model_dump(), procedure_id=pid, model_key=model_key, rate_limit=RATE),
            child_key=f"check:{input.session_id}:{input.revision}:{pid}",
        )
        for pid in r.to_check
    ]
    results = [CheckResult.model_validate(o["check_one"]) for o in check_wf.run_many(runs)]
    if not all(c.stored for c in results):
        return ProcessResult(session_id=input.session_id, status="stale", turn_count=0, skipped=True)
    return finish_session(_get_store(), input)


@process_session_wf.task(parents=[check], execution_timeout=timedelta(minutes=10), retries=1)
def patterns(input: SessionRef, ctx: Context) -> ProcessResult:
    c = ctx.task_output(check)
    if c.skipped or c.result is None:
        return c
    patterns_wf.run(input, child_key=f"patterns:{input.session_id}:{input.revision}")
    return c


@patterns_wf.task(execution_timeout=timedelta(minutes=10), retries=2, backoff_factor=2.0, backoff_max_seconds=60)
def assign_one(input: SessionRef, ctx: Context) -> dict:
    store = _get_store()
    out = store.patterns.assign_session(input.workspace_id, input.session_id, recluster=False)
    for pid in sorted({p for p, _ in out.due}):
        for rid in sorted({r for p, r in out.due if p == pid}):
            recluster_wf.run(ReclusterRef(workspace_id=input.workspace_id, procedure_id=pid, rule_id=rid), wait_for_result=False)
    for pid in sorted({f.procedure_id for f in _session_findings(store, input)} - {p for p, _ in out.due}):
        if store.patterns.themes_due(input.workspace_id, pid):
            recluster_wf.run(ReclusterRef(workspace_id=input.workspace_id, procedure_id=pid), wait_for_result=False)
    return {"assigned": out.assigned, "due": [list(d) for d in out.due]}


@recluster_wf.task(execution_timeout=timedelta(minutes=20), retries=2, backoff_factor=2.0, backoff_max_seconds=120)
def recluster_one(input: ReclusterRef, ctx: Context) -> dict:
    store = _get_store()
    done = bool(input.rule_id) and store.patterns.recluster(input.workspace_id, input.procedure_id, input.rule_id or "", force=input.force)
    themed = store.patterns.regroup_themes(input.workspace_id, input.procedure_id, force=done)
    return {"reclustered": done, "themed": themed}


@recluster_daily_wf.task(execution_timeout=timedelta(hours=2), retries=1)
def recluster_daily(input, ctx: Context) -> dict:
    store = _get_store()
    for ref in store.sessions.close_idle():  # open sessions that stopped streaming: check what came in
        trigger_process_session(ref)
    out = store.patterns.recluster_due()
    return {"reclustered": out.reclustered, "themed": out.themed}


def _session_findings(store, ref: SessionRef):
    with Session(store.engine) as db:
        return list(db.exec(select(Finding).where(Finding.session_id == ref.session_id)))


@route_wf.task(execution_timeout=timedelta(minutes=5), retries=3, backoff_factor=2.0, backoff_max_seconds=60, rate_limits=[MODEL_LIMIT])
def route_one(input: RouteRef, ctx: Context) -> RouteResult:
    return route_session(_get_store(), input)


@check_wf.task(execution_timeout=timedelta(minutes=10), retries=3, backoff_factor=2.0, backoff_max_seconds=60, rate_limits=[MODEL_LIMIT])
def check_one(input: CheckRef, ctx: Context) -> CheckResult:
    return check_procedure(_get_store(), input)


def trigger_process_session(ref: SessionRef) -> None:
    try:
        process_session_wf.run(ref, wait_for_result=False)
    except IdempotencyCollisionError:
        pass  # this revision is already queued or done


def start_worker() -> None:
    worker = hatchet.worker(
        "sopops-worker", slots=int(os.environ.get("SOPOPS_WORKER_SLOTS", "20")), workflows=[process_session_wf, route_wf, check_wf, patterns_wf, recluster_wf, recluster_daily_wf]
    )
    worker.start()
