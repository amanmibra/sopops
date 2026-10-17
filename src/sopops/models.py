"""Database tables, as SQLModel table models. API request and response shapes live in `schemas`, so these never leak.

The schema is managed by Alembic (`sopops/migrations`); `db.upgrade` brings a database to head on startup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Text
from sqlmodel import Field, SQLModel


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    workspace: str = Field(primary_key=True, max_length=128)
    current_release: int | None = Field(default=None, description="The release agents are served.")


class FileVersion(SQLModel, table=True):
    """One version of one file in a workspace's sopc folder. `content` is NULL in a version that deletes the file."""

    __tablename__ = "file_versions"

    workspace: str = Field(primary_key=True, max_length=128)
    path: str = Field(primary_key=True, max_length=512)
    version: int = Field(primary_key=True)
    content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    author: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


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


class Fetch(SQLModel, table=True):
    """A call-start lookup: which release and prompt hash an agent was served."""

    __tablename__ = "fetches"

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(max_length=128, index=True)
    agent: str = Field(max_length=256)
    release: int
    hash: str = Field(max_length=128)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


metadata = SQLModel.metadata
