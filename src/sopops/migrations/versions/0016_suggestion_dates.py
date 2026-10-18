"""Suggestions: when one was applied as a draft, its pull request opened, and seen merged.

Existing rows get their best-known date: the last update of an applied, open or merged suggestion (a merged one's
pull request date stays unknown).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.add_column(sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("pr_opened_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE suggestions SET applied_at = updated_at WHERE status = 'applied'")
    op.execute("UPDATE suggestions SET pr_opened_at = updated_at WHERE status = 'pr_open'")
    op.execute("UPDATE suggestions SET merged_at = updated_at WHERE status = 'merged'")


def downgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.drop_column("merged_at")
        batch.drop_column("pr_opened_at")
        batch.drop_column("applied_at")
