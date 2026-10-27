"""Delivery mode per platform (`platform_delivery`), in place of per agent. `agent_delivery` stays, read only for a
platform that has no setting of its own yet.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_delivery",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "platform"),
    )


def downgrade() -> None:
    op.drop_table("platform_delivery")
