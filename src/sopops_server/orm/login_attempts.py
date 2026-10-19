"""The `login_attempts` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel


class LoginAttempt(SQLModel, table=True):
    """A password sign-in that hasn't succeeded, for the rate limits: written before the password is checked, and
    deleted (with the address's others) when it matches."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email", "email", "created_at"),
        Index("ix_login_attempts_ip", "ip", "created_at"),
    )

    id: str = Field(primary_key=True, max_length=36)
    email: str = Field(max_length=320)
    ip: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
