"""The `fetches` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel


class Fetch(SQLModel, table=True):
    """A call-start lookup: which release and prompt hash an agent was served."""

    __tablename__ = "fetches"

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(max_length=128, index=True)
    agent: str = Field(max_length=256)
    release: int
    hash: str = Field(max_length=128)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
