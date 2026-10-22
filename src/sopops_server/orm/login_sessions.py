"""The `login_sessions` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel, Text


class LoginSession(SQLModel, table=True):
    """A signed-in browser: the session cookie's random token, stored as a sha256 hash. It slides: each use moves
    `expires_at` out again. Signing out sets `revoked_at`. `org_id` is the org the user is working in.

    `provider` is what signed this session in (`github`, `password`, a plugin's id), so signing out knows whose
    session to end as well as this one. `refresh_ciphertext` is that provider's refresh token, encrypted with
    SOPOPS_SECRET_KEY, set only when the provider needs it back to end its own session on the way out
    (`LoginProvider.end_session`). It is a credential: it is excluded from the model, so it can't reach an API
    response or a `model_dump`, and it is never logged."""

    __tablename__ = "login_sessions"
    __table_args__ = (
        Index("ix_login_sessions_user", "user_id"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_login_sessions_user", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    token_hash: str = Field(max_length=64, unique=True)
    user_id: str = Field(max_length=36)
    org_id: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_seen_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    provider: str | None = Field(default=None, max_length=32, description="The login provider that signed this session in.")
    refresh_ciphertext: str | None = Field(default=None, sa_column=Column(Text, nullable=True), exclude=True)
