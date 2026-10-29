"""The `file_versions` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class FileVersion(SQLModel, table=True):
    """One version of one file in a workspace's sop folder. `content` is NULL in a version that deletes the file."""

    __tablename__ = "file_versions"

    workspace: str = Field(primary_key=True, max_length=128)
    path: str = Field(primary_key=True, max_length=512)
    version: int = Field(primary_key=True)
    content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    author: str | None = Field(default=None, max_length=256)
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`author` keeps the name).")
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
