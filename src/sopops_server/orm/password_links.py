"""The `password_links` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel


class PasswordLink(SQLModel, table=True):
    """A link that sets the password for an email address: a reset an owner or admin made (`created_by`), one from
    `sopops reset-password`, or one emailed from "Forgot password?". The token is stored as a sha256 hash; the link
    works once (`used_at`), for an hour. `ip` is who asked, for the rate limits on emailed ones."""

    __tablename__ = "password_links"
    __table_args__ = (
        Index("ix_password_links_email", "email", "created_at"),
        Index("ix_password_links_ip", "ip", "created_at"),
    )

    id: str = Field(primary_key=True, max_length=36)
    token_hash: str = Field(max_length=64, unique=True)
    email: str = Field(max_length=320)
    ip: str | None = Field(default=None, max_length=64)
    created_by: str | None = Field(default=None, max_length=36, description="The owner or admin who made it; None when emailed or from the CLI.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
