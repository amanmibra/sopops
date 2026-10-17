"""Patterns (findings grouped per rule, split into named failure modes) and themes (patterns grouped by session phase).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_themes_workspace_procedure", "themes", ["workspace_id", "procedure_id"])
    op.create_table(
        "patterns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("theme_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clustered_count", sa.Integer(), nullable=False),
        sa.Column("clustered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patterns_workspace_rule", "patterns", ["workspace_id", "procedure_id", "rule_id"])
    op.create_index("ix_patterns_parent", "patterns", ["parent_id"])
    # insights filter sessions by release and agent within a window
    op.create_index("ix_sessions_workspace_release", "sessions", ["workspace_id", "release_id"])
    op.create_index("ix_sessions_workspace_agent_started", "sessions", ["workspace_id", "agent_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_workspace_agent_started", table_name="sessions")
    op.drop_index("ix_sessions_workspace_release", table_name="sessions")
    op.drop_index("ix_patterns_parent", table_name="patterns")
    op.drop_index("ix_patterns_workspace_rule", table_name="patterns")
    op.drop_table("patterns")
    op.drop_index("ix_themes_workspace_procedure", table_name="themes")
    op.drop_table("themes")
