"""Email sign-in: single-use sign-in links, stored hashed, with who asked for the rate limits.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("next", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_email_links_email", "email_links", ["email", "created_at"])
    op.create_index("ix_email_links_ip", "email_links", ["ip", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_links_ip", table_name="email_links")
    op.drop_index("ix_email_links_email", table_name="email_links")
    op.drop_table("email_links")
