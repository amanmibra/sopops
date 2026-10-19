"""Model spend: one row per model call (`model_calls`), with its tokens, cost, workspace, session and purpose.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_workspace_created", "model_calls", ["workspace_id", "created_at"])
    op.create_index("ix_model_calls_session", "model_calls", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_session", table_name="model_calls")
    op.drop_index("ix_model_calls_workspace_created", table_name="model_calls")
    op.drop_table("model_calls")
