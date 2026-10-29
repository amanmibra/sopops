"""Repo connections: a workspace's GitHub repository, to pull its sop project and open pull requests.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repo_connections",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("repo", sa.String(100), nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("auth", sa.String(16), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(16), nullable=False),
        sa.Column("pulled_sha", sa.String(64), nullable=True),
        sa.Column("pulled_digest", sa.String(64), nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace"),
    )


def downgrade() -> None:
    op.drop_table("repo_connections")
