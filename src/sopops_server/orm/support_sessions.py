"""The `support_sessions` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text


class SupportSession(SQLModel, table=True):
    """The audit trail of one support session: a staff member working in a customer's workspace, why (if they said), and for how long.

    One row is written when the session starts and closed (`ended_at`) when it ends or is found expired; rows are
    never deleted, so an org's history of who looked at its data stays complete. `staff_email` is copied from the
    user, so it survives a rename or a deleted account. `login_session_id` is the `login_sessions` row the staff
    member's browser holds in its `sopops_mimic` cookie. `requests` counts the requests served to the session and
    `last_path` is the last one seen; both are approximate (`sopops_server.auth.service.Accounts.current`)."""

    __tablename__ = "support_sessions"
    __table_args__ = (
        Index("ix_support_sessions_org_started", "org_id", "started_at"),
        Index("ix_support_sessions_login_session", "login_session_id"),
    )

    id: str = Field(primary_key=True, max_length=36)
    login_session_id: str = Field(max_length=36)
    staff_user_id: str = Field(max_length=36)
    staff_email: str = Field(max_length=320, description="Copied when the session started, so a rename doesn't lose who it was.")
    org_id: str = Field(max_length=36)
    reason: str = Field(sa_column=Column(Text, nullable=False))
    requests: int = Field(default=0, description="Requests served to the session, counted as they're served (approximate).")
    last_path: str | None = Field(default=None, max_length=512)
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    until: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False), description="When the session dies, whatever happens.")
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
