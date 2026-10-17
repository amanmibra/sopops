"""Pipeline inputs and outputs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionRef(BaseModel):
    workspace_id: str
    session_id: str
    revision: str = Field(description="The session's payload hash when this run was queued. A run whose revision is stale does nothing.")


class ProcessResult(BaseModel):
    session_id: str
    status: str
    turn_count: int
    skipped: bool = Field(False, description="The session was re-sent since this run was queued, so a newer run handles it.")
