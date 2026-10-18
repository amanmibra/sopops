"""The `platform_secrets` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class PlatformSecret(SQLModel, table=True):
    """A workspace's credential for a platform, encrypted with SOPOPS_SECRET_KEY (Fernet). Never returned by the API.
    `kind`: `api_key` (push, and the read-only test) or `webhook_secret` (verifies the platform's runtime webhooks)."""

    __tablename__ = "platform_secrets"

    workspace: str = Field(primary_key=True, max_length=128)
    platform: str = Field(primary_key=True, max_length=32)
    kind: str = Field(primary_key=True, max_length=32)
    ciphertext: str = Field(sa_column=Column(Text, nullable=False))
    hint: str = Field(max_length=16, description="The last few characters, to tell keys apart.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
