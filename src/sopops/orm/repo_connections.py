"""The `repo_connections` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class RepoConnection(SQLModel, table=True):
    """A workspace's GitHub repository: the sopc project at `path` on `base_branch`, pulled into drafts, and where
    suggestions open pull requests. One per workspace. `auth` is how sopops authenticates: `token` (a fine-grained
    personal access token, encrypted with SOPOPS_SECRET_KEY in `ciphertext`; never returned by the API). A GitHub App
    (`app`, with an installation id) is the planned second kind. `pulled_sha` and `pulled_digest` are the commit last
    pulled and a hash of the files it wrote, so pulling the same commit again is a no-op."""

    __tablename__ = "repo_connections"

    workspace: str = Field(primary_key=True, max_length=128)
    owner: str = Field(max_length=100)
    repo: str = Field(max_length=100)
    base_branch: str = Field(max_length=255)
    path: str = Field(max_length=512, description="The sopc project's folder in the repo; empty for the root.")
    auth: str = Field(max_length=16)
    ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    hint: str = Field(max_length=16, description="The token's last few characters, to tell tokens apart.")
    pulled_sha: str | None = Field(default=None, max_length=64)
    pulled_digest: str | None = Field(default=None, max_length=64)
    pulled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
