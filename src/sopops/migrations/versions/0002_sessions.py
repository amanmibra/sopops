"""Sessions (voice calls and text chats) and per-workspace ingest tokens.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(256), nullable=True),
        sa.Column("agent_ref", sa.String(256), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("version_ref", sa.String(256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("turns", JSONDoc, nullable=False),
        sa.Column("tool_calls", JSONDoc, nullable=False),
        sa.Column("metadata", JSONDoc, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "platform", "external_id", name="uq_sessions_key"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace"], name="fk_sessions_workspace"),
        sa.ForeignKeyConstraint(["workspace_id", "release_id"], ["releases.workspace", "releases.number"], name="fk_sessions_release"),
    )
    op.create_index("ix_sessions_workspace_started", "sessions", ["workspace_id", "started_at"])
    op.create_table(
        "ingest_tokens",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace"),
        sa.UniqueConstraint("token_hash"),
    )


def downgrade() -> None:
    op.drop_table("ingest_tokens")
    op.drop_index("ix_sessions_workspace_started", table_name="sessions")
    op.drop_table("sessions")
