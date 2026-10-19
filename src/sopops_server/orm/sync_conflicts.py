"""The `sync_conflicts` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class SyncConflict(SQLModel, table=True):
    """A file that changed both on GitHub and in the drafts since the last sync, and doesn't merge (`sopops_server.repo_sync`).
    The draft is left alone until someone keeps it, takes GitHub's, or edits it. `base` is the file at the commit synced
    before, `theirs` the file at `sha` (the commit that conflicted); either is NULL where the file didn't exist."""

    __tablename__ = "sync_conflicts"

    workspace: str = Field(primary_key=True, max_length=128)
    path: str = Field(primary_key=True, max_length=512)
    sha: str = Field(max_length=64)
    base: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    theirs: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
