"""The `workspaces` table."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    workspace: str = Field(primary_key=True, max_length=128)
    current_release: int | None = Field(default=None, description="The release agents are served.")
