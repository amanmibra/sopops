"""The `identities` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel, UniqueConstraint


class Identity(SQLModel, table=True):
    """A login provider's account for a user (`github`, `google`, `email` or `password`, or one a plugin registers). One
    user can have one per provider. `email` is the verified address the provider gave at the last sign-in.

    A `password` identity's `provider_user_id` is the user's id, and `password_hash` its argon2id hash (None for every
    other provider)."""

    __tablename__ = "identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_identities_provider_user"),
        Index("ix_identities_user", "user_id"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_identities_user", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    user_id: str = Field(max_length=36)
    provider: str = Field(max_length=32)
    provider_user_id: str = Field(max_length=255)
    email: str = Field(max_length=320)
    password_hash: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
