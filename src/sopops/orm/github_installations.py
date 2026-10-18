"""The `github_installations` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import BigInteger, Column, DateTime, Field, SQLModel


class GitHubInstallation(SQLModel, table=True):
    """An installation of the GitHub App that a workspace may connect repositories through: recorded when the install
    started from that workspace comes back to sopops (its signed `state` names the workspace). `account` is the user or
    organization it's installed on."""

    __tablename__ = "github_installations"

    workspace: str = Field(primary_key=True, max_length=128)
    installation_id: int = Field(sa_column=Column(BigInteger, primary_key=True, autoincrement=False))
    account: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
