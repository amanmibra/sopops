"""The `runtime_serves` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel


class RuntimeServe(SQLModel, table=True):
    """A prompt served to a platform's call-start webhook, keyed by the platform's call or conversation id, so the
    session that arrives later links to the release it ran on."""

    __tablename__ = "runtime_serves"

    workspace: str = Field(primary_key=True, max_length=128)
    platform: str = Field(primary_key=True, max_length=32)
    call_id: str = Field(primary_key=True, max_length=256)
    agent: str = Field(max_length=256)
    release: int
    hash: str = Field(max_length=128)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
