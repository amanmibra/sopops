"""The `workspaces` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, ForeignKeyConstraint, Index, SQLModel

from .columns import JSONDoc


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspaces_org", "org_id"),
        ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_workspaces_org"),
    )

    workspace: str = Field(primary_key=True, max_length=128)
    current_release: int | None = Field(default=None, description="The release agents are served.")
    org_id: str = Field(max_length=36, description="The org it belongs to.")
    spot_check_batch: int | None = Field(default=None, description="How many rule results a spot-check batch draws; null is the default (`labels.SPOT_CHECK_BATCH`).")
    analysis_paused_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True), description="Since when analysis is paused; null when it runs (`sopops_server.analysis`).")
    analysis_paused_by: str | None = Field(default=None, max_length=256, description="Who paused it.")
    analysis_skip_tags: list[str] = Field(default_factory=list, sa_column=Column(JSONDoc, nullable=False, server_default="[]"), description="Sessions with any of these tags aren't checked (`sopops_server.analysis`).")
