"""Suggestions: the file version an edit was merged into when the procedure had changed since it was written, and the
suggestion a rewrite replaces.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.add_column(sa.Column("merged_into_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rewrite_of", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.drop_column("rewrite_of")
        batch.drop_column("merged_into_version")
