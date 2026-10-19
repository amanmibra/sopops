"""The `github_deliveries` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel


class GitHubDelivery(SQLModel, table=True):
    """A webhook delivery already handled (`X-GitHub-Delivery`), so a redelivery is ignored. Kept for a week."""

    __tablename__ = "github_deliveries"

    id: str = Field(primary_key=True, max_length=64)
    event: str = Field(max_length=64)
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
