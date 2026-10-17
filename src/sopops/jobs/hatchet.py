"""All Hatchet wiring. Workflows wrap the plain functions in `sopops.pipeline`; nothing else imports hatchet_sdk.

Needs `pip install 'sopops[hatchet]'` and HATCHET_CLIENT_TOKEN (plus HATCHET_CLIENT_TLS_STRATEGY=none for a local
hatchet-lite). The worker (`sopops worker`) runs the tasks; the API only queues runs.
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

from ..pipeline import ProcessResult, SessionRef, process_session

hatchet = Hatchet()

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

_store = None


def _get_store():
    """The worker's store, from DATABASE_URL. The API process runs migrations; the worker doesn't."""
    global _store
    if _store is None:
        from ..db import database_url
        from ..sopc import Sopc
        from ..store import Store

        _store = Store(database_url(), Sopc(), migrate=False)
    return _store


@process_session_wf.task(execution_timeout=timedelta(minutes=2), retries=3, backoff_factor=2.0, backoff_max_seconds=30)
def normalize(input: SessionRef, ctx: Context) -> ProcessResult:
    return process_session(_get_store(), input)


def trigger_process_session(ref: SessionRef) -> None:
    try:
        process_session_wf.run_no_wait(ref)
    except IdempotencyCollisionError:
        pass  # this revision is already queued or done


def start_worker() -> None:
    worker = hatchet.worker("sopops-worker", slots=int(os.environ.get("SOPOPS_WORKER_SLOTS", "20")), workflows=[process_session_wf])
    worker.start()
