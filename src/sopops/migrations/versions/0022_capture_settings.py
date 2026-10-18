"""Sessions with no code in the agent: per workspace and platform, the LiveKit project and agent mapping, and
ElevenLabs Enterprise live monitoring with how it last went.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capture_settings",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("agent_map", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("live_state", sa.String(16), nullable=True),
        sa.Column("live_detail", sa.Text(), nullable=True),
        sa.Column("live_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace", "platform"),
    )


def downgrade() -> None:
    op.drop_table("capture_settings")
