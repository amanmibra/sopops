"""The `memberships` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel


class Membership(SQLModel, table=True):
    """A user's place in an org. `role`: `owner` or `admin` (manage members, invites and the org's settings) or
    `member` (everything else)."""

    __tablename__ = "memberships"
    __table_args__ = (
        Index("ix_memberships_user", "user_id"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_memberships_org", ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user", ondelete="CASCADE"),
    )

    org_id: str = Field(primary_key=True, max_length=36)
    user_id: str = Field(primary_key=True, max_length=36)
    role: str = Field(max_length=16)  # owner | admin | member
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
