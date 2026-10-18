"""The `releases` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, DateTime, Field, SQLModel, Text


class Release(SQLModel, table=True):
    """An immutable {path: version} snapshot plus its compiled build."""

    __tablename__ = "releases"

    workspace: str = Field(primary_key=True, max_length=128)
    number: int = Field(primary_key=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    author: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    files: dict[str, int] = Field(sa_column=Column(JSON, nullable=False))
    # {"agents": {id: {platform_ref, hash, prompt, tools, sops, components}}}
    build: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
