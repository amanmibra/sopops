"""The session pipeline: normalize, route, check, finish, patterns. Each step is a plain function that takes and returns Pydantic
models, so it runs the same inline (no Hatchet) and as a Hatchet task (`sopops.jobs.hatchet`)."""

from .process import (
    assign_patterns,
    check_procedure,
    finish_session,
    normalize_session,
    process_session,
    route_session,
    session_event,
)
from .types import (
    CheckRef,
    CheckResult,
    ProcessResult,
    ReclusterRef,
    RouteRef,
    RouteResult,
    SessionRef,
)

__all__ = [
    "CheckRef", "CheckResult", "ProcessResult", "ReclusterRef", "RouteRef", "RouteResult", "SessionRef",
    "assign_patterns", "check_procedure", "finish_session", "normalize_session", "process_session", "route_session", "session_event",
]
