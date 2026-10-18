"""The `invites` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel


class Invite(SQLModel, table=True):
    """An invite to join an org with a role, for one email address. The link's token is stored as a sha256 hash and
    shown once, when made. Accepted or revoked invites are kept."""

    __tablename__ = "invites"
    __table_args__ = (
        Index("ix_invites_org", "org_id"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_invites_org", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    org_id: str = Field(max_length=36)
    email: str = Field(max_length=320)
    role: str = Field(max_length=16)
    token_hash: str = Field(max_length=64, unique=True)
    invited_by: str | None = Field(default=None, max_length=36, description="The user who made it.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    accepted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    accepted_by: str | None = Field(default=None, max_length=36)
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
