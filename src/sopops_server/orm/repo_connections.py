"""The `repo_connections` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import BigInteger, Column, DateTime, Field, SQLModel, Text


class RepoConnection(SQLModel, table=True):
    """A workspace's GitHub repository: the sop project at `path` on `base_branch`, pulled into drafts, and where
    suggestions open pull requests. One per workspace. `auth` is how sopops authenticates: `token` (a fine-grained
    personal access token, encrypted with SOPOPS_SECRET_KEY in `ciphertext`; never returned by the API) or `app` (the
    GitHub App's `installation_id`; `ciphertext` and `hint` are empty). `pulled_sha` and `pulled_digest` are the commit
    last pulled or synced and a hash of its files, so pulling the same commit again is a no-op; that commit is also the
    base of the next sync's three-way merge (`sopops_server.repo_sync`).

    `broken` says why the connection stopped working (the App was uninstalled, suspended, or lost the repository), set
    by webhooks and cleared by reconnecting. `webhook_at` is when GitHub last sent a webhook for the repository: while
    recent, the pollers back off. `sync_pr_*` follow the one long-lived pull request that sends drafts to GitHub."""

    __tablename__ = "repo_connections"

    workspace: str = Field(primary_key=True, max_length=128)
    owner: str = Field(max_length=100)
    repo: str = Field(max_length=100)
    base_branch: str = Field(max_length=255)
    path: str = Field(max_length=512, description="The sop project's folder in the repo; empty for the root.")
    auth: str = Field(max_length=16)
    ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    hint: str = Field(max_length=16, description="The token's last few characters, to tell tokens apart.")
    pulled_sha: str | None = Field(default=None, max_length=64)
    pulled_digest: str | None = Field(default=None, max_length=64)
    pulled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    installation_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    broken: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sync_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    webhook_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    polled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    sync_pr_url: str | None = Field(default=None, max_length=1024)
    sync_pr_state: str | None = Field(default=None, max_length=16)  # open | merged | closed
    sync_pr_checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
