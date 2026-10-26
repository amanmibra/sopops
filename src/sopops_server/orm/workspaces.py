"""The `workspaces` table."""

from __future__ import annotations

from sqlmodel import Field, ForeignKeyConstraint, Index, SQLModel


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
