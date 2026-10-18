"""Verdicts on patterns, overrides on findings, suggested procedure edits; suppressed findings and the checker's own
result kept next to the reviewed one.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "verdicts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("pattern_id", sa.String(36), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(256), nullable=True),
        sa.Column("carried_from", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_verdicts_workspace_pattern", "verdicts", ["workspace_id", "pattern_id", "created_at"])
    op.create_table(
        "finding_overrides",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("finding_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_finding_overrides_session", ondelete="CASCADE"),
    )
    op.create_index("ix_finding_overrides_session_rule", "finding_overrides", ["session_id", "procedure_id", "rule_id"])
    op.create_index("ix_finding_overrides_workspace", "finding_overrides", ["workspace_id", "created_at"])
    op.create_table(
        "suggestions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("pattern_id", sa.String(36), nullable=False),
        sa.Column("verdict_id", sa.String(36), nullable=True),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("path", sa.String(512), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("old_text", sa.Text(), nullable=True),
        sa.Column("new_text", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("plan", JSONDoc, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pr_url", sa.String(1024), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(256), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suggestions_workspace_status", "suggestions", ["workspace_id", "status"])
    op.create_index("ix_suggestions_pattern", "suggestions", ["pattern_id"])
    with op.batch_alter_table("findings") as batch:
        batch.add_column(sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("procedure_checks") as batch:
        batch.add_column(sa.Column("checked_result", sa.String(16), nullable=True))
    op.execute("UPDATE procedure_checks SET checked_result = result")


def downgrade() -> None:
    with op.batch_alter_table("procedure_checks") as batch:
        batch.drop_column("checked_result")
    with op.batch_alter_table("findings") as batch:
        batch.drop_column("suppressed")
    op.drop_index("ix_suggestions_pattern", table_name="suggestions")
    op.drop_index("ix_suggestions_workspace_status", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_index("ix_finding_overrides_workspace", table_name="finding_overrides")
    op.drop_index("ix_finding_overrides_session_rule", table_name="finding_overrides")
    op.drop_table("finding_overrides")
    op.drop_index("ix_verdicts_workspace_pattern", table_name="verdicts")
    op.drop_table("verdicts")
