"""A workspace's own AI keys and models, over the deployment's defaults (`ai_settings`).

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("typesafe_ciphertext", sa.Text(), nullable=True),
        sa.Column("typesafe_hint", sa.String(16), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_ciphertext", sa.Text(), nullable=True),
        sa.Column("provider_hint", sa.String(16), nullable=True),
        sa.Column("api_base", sa.String(1024), nullable=True),
        sa.Column("fast_model", sa.String(256), nullable=True),
        sa.Column("reasoning_model", sa.String(256), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_settings")
