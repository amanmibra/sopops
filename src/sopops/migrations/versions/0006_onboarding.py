"""Onboarding: imports (LLM jobs and coding-agent sessions with onboarding tokens) and their progress events.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("repo", sa.String(1024), nullable=True),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pushes", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("token_prefix", sa.String(32), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_imports_workspace_created", "imports", ["workspace", "created_at"])
    op.create_table(
        "import_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.String(36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("by", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_events_import_id", "import_events", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_import_events_import_id", table_name="import_events")
    op.drop_table("import_events")
    op.drop_index("ix_imports_workspace_created", table_name="imports")
    op.drop_table("imports")
