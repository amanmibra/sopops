"""Passwords: a `password` identity's argon2id hash, single-use links that set a password, and failed sign-ins for the
rate limits.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("identities", sa.Column("password_hash", sa.String(255), nullable=True))
    op.create_table(
        "password_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_password_links_email", "password_links", ["email", "created_at"])
    op.create_index("ix_password_links_ip", "password_links", ["ip", "created_at"])
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email", "created_at"])
    op.create_index("ix_login_attempts_ip", "login_attempts", ["ip", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempts_ip", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_password_links_ip", table_name="password_links")
    op.drop_index("ix_password_links_email", table_name="password_links")
    op.drop_table("password_links")
    with op.batch_alter_table("identities") as batch:
        batch.drop_column("password_hash")
