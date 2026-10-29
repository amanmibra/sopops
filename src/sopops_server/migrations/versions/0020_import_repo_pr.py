"""Imports: the repository an onboarding in repo mode writes to (branch, folder, and whether the folder already held a
sop project), and the onboarding pull request the coding agent opened, followed on GitHub.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imports") as batch:
        batch.add_column(sa.Column("repo_branch", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("repo_path", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("repo_project", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("pr_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("pr_state", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("pr_checked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("pr_merged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imports") as batch:
        for name in ("pr_merged_at", "pr_checked_at", "pr_state", "pr_url", "repo_project", "repo_path", "repo_branch"):
            batch.drop_column(name)
