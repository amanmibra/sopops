"""The `join_links` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel


class JoinLink(SQLModel, table=True):
    """A team's join link: anyone who opens it can make an account, or sign in, and join the org with `role`. Not tied
    to an email, and used any number of times until it expires or is turned off (`revoked_at`). An org has at most one
    that works: making a new one turns the old one off. The token is stored as a sha256 hash and shown once, when
    made. `joined` counts the people who joined with it."""

    __tablename__ = "join_links"
    __table_args__ = (
        Index("ix_join_links_org", "org_id"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_join_links_org", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    org_id: str = Field(max_length=36)
    token_hash: str = Field(max_length=64, unique=True)
    role: str = Field(max_length=16)
    joined: int = Field(default=0)
    created_by: str | None = Field(default=None, max_length=36, description="The owner or admin who made it.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
