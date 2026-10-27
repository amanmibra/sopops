"""The `platform_delivery` table."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class PlatformDelivery(SQLModel, table=True):
    """How a platform's agents get their prompt: `pull` (its call-start webhook fetches it), `push` (sopops updates
    each agent on the platform when a release goes live) or `both`. One setting per platform; no row means `pull`,
    unless agents still have the older per-agent setting (`agent_delivery`)."""

    __tablename__ = "platform_delivery"

    workspace: str = Field(primary_key=True, max_length=128)
    platform: str = Field(primary_key=True, max_length=32)
    mode: str = Field(max_length=8)
