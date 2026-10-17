"""Runtime delivery: platform keys and webhook secrets (encrypted), per-agent delivery mode, push results,
and the prompts served to platform webhooks.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_secrets",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "platform", "kind"),
    )
    op.create_table(
        "agent_delivery",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("agent", sa.String(256), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "agent"),
    )
    op.create_table(
        "push_results",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("release", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(256), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("platform_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version_id", sa.String(256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "release", "agent"),
    )
    op.create_table(
        "runtime_serves",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("call_id", sa.String(256), nullable=False),
        sa.Column("agent", sa.String(256), nullable=False),
        sa.Column("release", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(128), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "platform", "call_id"),
    )
    op.create_index("ix_runtime_serves_at", "runtime_serves", ["at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_serves_at", table_name="runtime_serves")
    op.drop_table("runtime_serves")
    op.drop_table("push_results")
    op.drop_table("agent_delivery")
    op.drop_table("platform_secrets")
