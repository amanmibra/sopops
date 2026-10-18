"""The `procedure_checks` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class ProcedureCheck(SQLModel, table=True):
    """One session x one procedure the router considered: whether it applied, was excluded (the session ended before
    the procedure started or finished), and, once checked, the rolled-up result. Rewritten whole by each run."""

    __tablename__ = "procedure_checks"
    __table_args__ = (
        UniqueConstraint("session_id", "procedure_id", name="uq_procedure_checks_session_procedure"),
        Index("ix_procedure_checks_workspace_procedure", "workspace_id", "procedure_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_procedure_checks_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    procedure_name: str | None = Field(default=None, max_length=512)
    procedure_version: int | None = Field(default=None, description="The procedure file's version in the release it was checked against.")
    release_id: int | None = Field(default=None, description="The release the procedure was read from.")
    applicable: bool
    excluded_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    route_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    result: str | None = Field(default=None, max_length=16)  # deviated | partial | followed | not_measurable; null until checked
    goal_reached: bool | None = None
    goal_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    steps_applicable: int | None = None
    steps_done: int | None = None
    nevers_violated: int | None = None
    model: str | None = Field(default=None, max_length=256, description="The model that made the last call for this row.")
    prompt_version: str | None = Field(default=None, max_length=64)
    revision: str = Field(max_length=64, description="The session payload hash this row was computed from.")
    checked_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
