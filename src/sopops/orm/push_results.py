"""The `push_results` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class PushResult(SQLModel, table=True):
    """The latest push of one agent's prompt in one release to its platform."""

    __tablename__ = "push_results"

    workspace: str = Field(primary_key=True, max_length=128)
    release: int = Field(primary_key=True)
    agent: str = Field(primary_key=True, max_length=256)
    platform: str = Field(max_length=32)
    platform_id: str = Field(max_length=256)
    status: str = Field(max_length=16)  # ok | unchanged | error
    version_id: str | None = Field(default=None, max_length=256, description="The platform's version id, if it returned one.")
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempts: int = 1
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
