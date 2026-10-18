"""The `imports` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, DateTime, Field, Index, SQLModel, Text


class ImportRecord(SQLModel, table=True):
    """An import of existing agent prompts into a workspace.

    `source` is `llm` (sopops extracts with its own model) or `agent` (a coding agent such as Claude Code extracts
    in the user's repo and pushes a proposal with an onboarding token). Either way the result is a proposal the user
    reviews, then applies as drafts.

    In repo mode the workspace's GitHub connection is recorded (`repo` as owner/name, `repo_branch`, `repo_path`) with
    what the folder held when the import started (`repo_project`: `missing`, `invalid` or `valid`). When it had no
    buildable project, the coding agent opens a pull request adding one and reports it (`pr_url`); sopops follows it
    (`pr_state`: `open`, `merged`, `closed`) and pulls the folder into drafts when it merges.
    """

    __tablename__ = "imports"
    __table_args__ = (Index("ix_imports_workspace_created", "workspace", "created_at"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace: str = Field(max_length=128)
    source: str = Field(max_length=16)  # llm | agent
    status: str = Field(max_length=16)  # waiting | running | ready | failed | applied | discarded
    mode: str = Field(default="hosted", max_length=16)  # hosted | repo
    repo: str | None = Field(default=None, max_length=1024, description="Repo mode: the connected repository (owner/name).")
    repo_branch: str | None = Field(default=None, max_length=255)
    repo_path: str | None = Field(default=None, max_length=512, description="The sopc project's folder in the repo; empty for the root.")
    repo_project: str | None = Field(default=None, max_length=16)  # missing | invalid | valid, when the import started
    agents: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))  # names, for lists
    proposal: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    pushes: int = 0
    pr_url: str | None = Field(default=None, max_length=1024, description="The onboarding pull request the coding agent opened.")
    pr_state: str | None = Field(default=None, max_length=16)  # open | merged | closed
    pr_checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    pr_merged_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))  # when sopops saw it merged
    # The onboarding token (source `agent`) is a `tokens` row with this import's id (scope onboarding:write).
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_by: str | None = Field(default=None, max_length=256)
    author_id: str | None = Field(default=None, max_length=36, description="The signed-in user who did it (`created_by` keeps the name).")
