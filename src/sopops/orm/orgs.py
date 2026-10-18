"""The `orgs` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import JSON, Column, DateTime, Field, SQLModel


class Org(SQLModel, table=True):
    """A team: it owns workspaces, and people join it as members. `allowed_domains`: email domains (`example.com`)
    whose verified addresses may join as members without an invite."""

    __tablename__ = "orgs"

    id: str = Field(primary_key=True, max_length=36)
    name: str = Field(max_length=128)
    slug: str = Field(max_length=128, unique=True)
    allowed_domains: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
