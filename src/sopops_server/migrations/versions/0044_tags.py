"""Tags: `agent_tags` (labels set on an agent in sopops) and `session_tags` (a session's copy, taken when it came in,
plus any its sender passed), so sessions and Insights filter by them.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_tags",
        sa.Column("workspace", sa.String(128), primary_key=True),
        sa.Column("agent", sa.String(256), primary_key=True),
        sa.Column("tag", sa.String(64), primary_key=True),
    )
    op.create_table(
        "session_tags",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("tag", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_session_tags_session", ondelete="CASCADE"),
    )
    op.create_index("ix_session_tags_workspace_tag", "session_tags", ["workspace_id", "tag"])


def downgrade() -> None:
    op.drop_index("ix_session_tags_workspace_tag", table_name="session_tags")
    op.drop_table("session_tags")
    op.drop_table("agent_tags")
