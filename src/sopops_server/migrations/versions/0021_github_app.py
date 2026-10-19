"""GitHub App: installation auth for repo connections, the App itself when created from sopops, the installations a
workspace may use, handled webhook deliveries, two-way sync (conflicts, the sync pull request) and broken connections.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("repo_connections") as batch:
        batch.add_column(sa.Column("installation_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("broken", sa.Text(), nullable=True))
        batch.add_column(sa.Column("sync_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("webhook_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("polled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("sync_pr_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("sync_pr_state", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("sync_pr_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "github_apps",
        sa.Column("id", sa.String(16), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner", sa.String(100), nullable=True),
        sa.Column("html_url", sa.String(1024), nullable=True),
        sa.Column("key_ciphertext", sa.Text(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "github_installations",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("account", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "installation_id"),
    )
    op.create_table(
        "github_deliveries",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_github_deliveries_received_at", "github_deliveries", ["received_at"])
    op.create_table(
        "sync_conflicts",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("base", sa.Text(), nullable=True),
        sa.Column("theirs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "path"),
    )


def downgrade() -> None:
    op.drop_table("sync_conflicts")
    op.drop_index("ix_github_deliveries_received_at", table_name="github_deliveries")
    op.drop_table("github_deliveries")
    op.drop_table("github_installations")
    op.drop_table("github_apps")
    with op.batch_alter_table("repo_connections") as batch:
        for name in ("sync_pr_checked_at", "sync_pr_state", "sync_pr_url", "polled_at", "webhook_at", "sync_error", "broken", "installation_id"):
            batch.drop_column(name)
