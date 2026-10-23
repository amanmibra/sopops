"""Read-only support sessions: a staff member looking at one customer org to help them.

`orgs.support_access` is the customer's consent (on for every existing org, as it is for a new one). A login session
with `mimic_until` set is a support session, held in a second cookie beside the staff member's own. `support_sessions`
is the audit trail: one row per session, kept after it ends.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("support_access", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("login_sessions") as batch:
        batch.add_column(sa.Column("mimic_org_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("mimic_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("mimic_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "support_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("login_session_id", sa.String(36), nullable=False),
        sa.Column("staff_user_id", sa.String(36), nullable=False),
        sa.Column("staff_email", sa.String(320), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_path", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_sessions_org_started", "support_sessions", ["org_id", "started_at"])
    op.create_index("ix_support_sessions_login_session", "support_sessions", ["login_session_id"])


def downgrade() -> None:
    op.drop_index("ix_support_sessions_login_session", table_name="support_sessions")
    op.drop_index("ix_support_sessions_org_started", table_name="support_sessions")
    op.drop_table("support_sessions")
    with op.batch_alter_table("login_sessions") as batch:
        batch.drop_column("mimic_until")
        batch.drop_column("mimic_reason")
        batch.drop_column("mimic_org_id")
    with op.batch_alter_table("orgs") as batch:
        batch.drop_column("support_access")
