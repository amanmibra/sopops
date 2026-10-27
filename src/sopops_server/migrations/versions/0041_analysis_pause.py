"""A workspace's analysis can be paused (`workspaces.analysis_paused_at`, `analysis_paused_by`): sessions still come in,
and nothing is checked until it's resumed.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("analysis_paused_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("analysis_paused_by", sa.String(256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("analysis_paused_by")
        batch.drop_column("analysis_paused_at")
