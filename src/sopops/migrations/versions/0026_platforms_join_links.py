"""The platforms a team uses (`orgs.platforms`, so the app shows only those), and each team's join link
(`join_links`). Existing orgs keep `platforms` NULL (never picked), which shows every platform, as before.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("platforms", sa.JSON(), nullable=True))
    op.create_table(
        "join_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("joined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_join_links_org", ondelete="CASCADE"),
    )
    op.create_index("ix_join_links_org", "join_links", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_join_links_org", table_name="join_links")
    op.drop_table("join_links")
    with op.batch_alter_table("orgs") as batch:
        batch.drop_column("platforms")
