"""The session pipeline: normalize, route, check, finish. Each step is a plain function that takes and returns Pydantic
models, so it runs the same inline (no Hatchet) and as a Hatchet task (`sopops.jobs.hatchet`)."""

from .models import CheckRef, CheckResult, ProcessResult, RouteRef, RouteResult, SessionRef
from .process import check_procedure, finish_session, normalize_session, process_session, route_session

__all__ = [
    "CheckRef", "CheckResult", "ProcessResult", "RouteRef", "RouteResult", "SessionRef",
    "check_procedure", "finish_session", "normalize_session", "process_session", "route_session",
]
