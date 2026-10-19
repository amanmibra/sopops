"""Rule checks: each rule rewritten as literal yes/no checks for Jev, once per rule version.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "rule_checks",
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("actions", JSONDoc, nullable=False),
        sa.Column("forbidden", JSONDoc, nullable=False),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "rule_id", "text_hash"),
    )


def downgrade() -> None:
    op.drop_table("rule_checks")
