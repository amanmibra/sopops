"""The session pipeline. Each step is a plain function that takes and returns Pydantic models, so it runs the same
inline (no Hatchet) and as a Hatchet task (`sopops.jobs.hatchet`). M1 has one step, normalize; route and check follow."""

from .models import ProcessResult, SessionRef
from .process import process_session

__all__ = ["ProcessResult", "SessionRef", "process_session"]
