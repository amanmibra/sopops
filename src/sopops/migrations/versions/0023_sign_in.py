"""Sign-in: users, their login identities, orgs and memberships, invites and login sessions. Workspaces belong to an
org: one org is made for the workspaces there are (named after the first one, else "Default"). Where an author is
recorded, `author_id` keeps the signed-in user next to the name.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-18
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Where an author is recorded, and the column with the name.
AUTHORED = ("file_versions", "releases", "verdicts", "suggestions", "finding_overrides", "imports", "tokens")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("avatar_url", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "identities",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_identities_provider_user"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_identities_user", ondelete="CASCADE"),
    )
    op.create_index("ix_identities_user", "identities", ["user_id"])
    op.create_table(
        "orgs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("allowed_domains", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "memberships",
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "user_id"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_memberships_org", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user", ondelete="CASCADE"),
    )
    op.create_index("ix_memberships_user", "memberships", ["user_id"])
    op.create_table(
        "invites",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.String(36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_invites_org", ondelete="CASCADE"),
    )
    op.create_index("ix_invites_org", "invites", ["org_id"])
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_login_sessions_user", ondelete="CASCADE"),
    )
    op.create_index("ix_login_sessions_user", "login_sessions", ["user_id"])

    # One org for the workspaces there are, named after the first.
    conn = op.get_bind()
    first = conn.execute(sa.text("SELECT workspace FROM workspaces ORDER BY workspace LIMIT 1")).scalar()
    org_id = str(uuid.uuid4())
    name = first or "Default"
    orgs = sa.table("orgs", sa.column("id", sa.String()), sa.column("name", sa.String()), sa.column("slug", sa.String()), sa.column("allowed_domains", sa.JSON()),
                    sa.column("created_at", sa.DateTime(timezone=True)))
    op.bulk_insert(orgs, [{"id": org_id, "name": name, "slug": _slug(name), "allowed_domains": [], "created_at": datetime.now(timezone.utc)}])

    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("org_id", sa.String(36), nullable=True))
    conn.execute(sa.text("UPDATE workspaces SET org_id = :org"), {"org": org_id})
    with op.batch_alter_table("workspaces") as batch:
        batch.alter_column("org_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key("fk_workspaces_org", "orgs", ["org_id"], ["id"])
        batch.create_index("ix_workspaces_org", ["org_id"])

    for table in AUTHORED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("author_id", sa.String(36), nullable=True))


def downgrade() -> None:
    for table in AUTHORED:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("author_id")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_index("ix_workspaces_org")
        batch.drop_constraint("fk_workspaces_org", type_="foreignkey")
        batch.drop_column("org_id")
    op.drop_index("ix_login_sessions_user", table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_index("ix_invites_org", table_name="invites")
    op.drop_table("invites")
    op.drop_index("ix_memberships_user", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("orgs")
    op.drop_index("ix_identities_user", table_name="identities")
    op.drop_table("identities")
    op.drop_table("users")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64] or "default"
