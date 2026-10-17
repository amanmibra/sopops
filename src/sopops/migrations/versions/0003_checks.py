"""SOP checks: procedure_checks (session x procedure), rule_results (session x rule), findings, and sessions.result.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("result", sa.String(16), nullable=True))
    op.create_table(
        "procedure_checks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("procedure_name", sa.String(512), nullable=True),
        sa.Column("procedure_version", sa.Integer(), nullable=True),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("applicable", sa.Boolean(), nullable=False),
        sa.Column("excluded_reason", sa.Text(), nullable=True),
        sa.Column("route_reason", sa.Text(), nullable=True),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("goal_reached", sa.Boolean(), nullable=True),
        sa.Column("goal_reason", sa.Text(), nullable=True),
        sa.Column("steps_applicable", sa.Integer(), nullable=True),
        sa.Column("steps_done", sa.Integer(), nullable=True),
        sa.Column("nevers_violated", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "procedure_id", name="uq_procedure_checks_session_procedure"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_procedure_checks_session", ondelete="CASCADE"),
    )
    op.create_index("ix_procedure_checks_workspace_procedure", "procedure_checks", ["workspace_id", "procedure_id"])
    op.create_table(
        "rule_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("rule_text_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence", JSONDoc, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "procedure_id", "rule_id", name="uq_rule_results_session_rule"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_rule_results_session", ondelete="CASCADE"),
    )
    op.create_index("ix_rule_results_procedure_rule", "rule_results", ["workspace_id", "procedure_id", "rule_id", "session_id"])
    op.create_table(
        "findings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_result_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pattern_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_findings_session", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_result_id"], ["rule_results.id"], name="fk_findings_rule_result", ondelete="CASCADE"),
    )
    op.create_index("ix_findings_workspace_rule", "findings", ["workspace_id", "procedure_id", "rule_id"])
    op.create_index("ix_findings_pattern", "findings", ["pattern_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_pattern", table_name="findings")
    op.drop_index("ix_findings_workspace_rule", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_rule_results_procedure_rule", table_name="rule_results")
    op.drop_table("rule_results")
    op.drop_index("ix_procedure_checks_workspace_procedure", table_name="procedure_checks")
    op.drop_table("procedure_checks")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("result")
