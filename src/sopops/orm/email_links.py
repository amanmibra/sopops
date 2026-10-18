"""The `email_links` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel


class EmailLink(SQLModel, table=True):
    """A sign-in link sent by email. The token is stored as a sha256 hash; the link works once (`used_at`), for 15
    minutes. `ip` is who asked, for the per-address rate limit; `next` is the page to open after signing in."""

    __tablename__ = "email_links"
    __table_args__ = (
        Index("ix_email_links_email", "email", "created_at"),
        Index("ix_email_links_ip", "ip", "created_at"),
    )

    id: str = Field(primary_key=True, max_length=36)
    token_hash: str = Field(max_length=64, unique=True)
    email: str = Field(max_length=320)
    ip: str | None = Field(default=None, max_length=64)
    next: str = Field(default="/", max_length=512)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
