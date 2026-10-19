"""Initial schema: workspaces, file versions, releases and the fetch log.

The same tables sopops created on startup before it used Alembic; `sopops_server.db.upgrade` stamps such a database
with this revision instead of creating them again.

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("current_release", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace"),
    )
    op.create_table(
        "file_versions",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author", sa.String(256), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("workspace", "path", "version"),
    )
    op.create_table(
        "releases",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author", sa.String(256), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("build", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "number"),
    )
    op.create_table(
        "fetches",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("agent", sa.String(256), nullable=False),
        sa.Column("release", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(128), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fetches_workspace", "fetches", ["workspace"])


def downgrade() -> None:
    op.drop_index("ix_fetches_workspace", table_name="fetches")
    op.drop_table("fetches")
    op.drop_table("releases")
    op.drop_table("file_versions")
    op.drop_table("workspaces")
