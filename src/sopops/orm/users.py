"""The `users` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel


class User(SQLModel, table=True):
    """Someone who signs in. Made at their first sign-in from the login provider's profile; `email` is a verified
    address, lowercased, and links a second provider's identity to this user when it's the same."""

    __tablename__ = "users"

    id: str = Field(primary_key=True, max_length=36)
    email: str = Field(max_length=320, unique=True)
    name: str = Field(default="", max_length=256)
    avatar_url: str | None = Field(default=None, max_length=1024)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_login_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
