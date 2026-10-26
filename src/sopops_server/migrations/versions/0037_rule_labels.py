"""Reviewer labels on rule results: the right status on an override, corrections and spot checks on any rule
(`rule_labels`), what the decision said before review on each rule result, and a workspace's spot-check rate.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("finding_overrides") as batch:
        batch.add_column(sa.Column("status", sa.String(16), nullable=True))
    with op.batch_alter_table("rule_results") as batch:
        batch.add_column(sa.Column("decision_p", sa.Float(), nullable=True))
        batch.add_column(sa.Column("decision_threshold", sa.Float(), nullable=True))
        batch.add_column(sa.Column("decided_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("reviewed", sa.Boolean(), nullable=True))
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("spot_check_rate", sa.Float(), nullable=True))
    op.create_table(
        "rule_labels",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_result_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("checked_status", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(256), nullable=True),
        sa.Column("author_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_rule_labels_session", ondelete="CASCADE"),
    )
    op.create_index("ix_rule_labels_session_rule", "rule_labels", ["session_id", "procedure_id", "rule_id"])
    op.create_index("ix_rule_labels_workspace", "rule_labels", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rule_labels_workspace", table_name="rule_labels")
    op.drop_index("ix_rule_labels_session_rule", table_name="rule_labels")
    op.drop_table("rule_labels")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("spot_check_rate")
    with op.batch_alter_table("rule_results") as batch:
        batch.drop_column("reviewed")
        batch.drop_column("decided_status")
        batch.drop_column("decision_threshold")
        batch.drop_column("decision_p")
    with op.batch_alter_table("finding_overrides") as batch:
        batch.drop_column("status")
