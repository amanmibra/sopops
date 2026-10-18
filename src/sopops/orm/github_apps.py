"""The `github_apps` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class GitHubApp(SQLModel, table=True):
    """The GitHub App sopops signs in as, when it was created from sopops (GitHub's App manifest flow) rather than set
    in the environment (SOPOPS_GITHUB_APP_ID and the rest, which win). One row, id `default`. The private key and the
    webhook secret are encrypted with SOPOPS_SECRET_KEY, and never returned or logged."""

    __tablename__ = "github_apps"

    id: str = Field(primary_key=True, max_length=16)
    app_id: str = Field(max_length=32)
    slug: str = Field(max_length=255)
    name: str = Field(max_length=255)
    owner: str | None = Field(default=None, max_length=100, description="The user or organization that owns the App.")
    html_url: str | None = Field(default=None, max_length=1024)
    key_ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    secret_ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
