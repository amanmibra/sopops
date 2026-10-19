"""Calibrations: fitted decision thresholds per workspace and program.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "calibrations",
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("program", sa.String(300), nullable=False),
        sa.Column("params", JSONDoc, nullable=False),
        sa.Column("questions", JSONDoc, nullable=False),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("report", JSONDoc, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "program"),
    )


def downgrade() -> None:
    op.drop_table("calibrations")
