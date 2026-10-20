"""What each session ran with, for comparing an agent's versions: `sessions.test`, `config` and `config_hash`, and
`prompt_texts` for prompts reported in a config.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("sessions", sa.Column("test", sa.String(128), nullable=True))
    op.add_column("sessions", sa.Column("config", JSONDoc, nullable=True))
    op.add_column("sessions", sa.Column("config_hash", sa.String(16), nullable=True))
    op.create_index("ix_sessions_workspace_config", "sessions", ["workspace_id", "config_hash"])
    op.create_index("ix_sessions_workspace_test", "sessions", ["workspace_id", "test"])
    op.create_table(
        "prompt_texts",
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column("hash", sa.String(16), primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("prompt_texts")
    op.drop_index("ix_sessions_workspace_test", "sessions")
    op.drop_index("ix_sessions_workspace_config", "sessions")
    op.drop_column("sessions", "config_hash")
    op.drop_column("sessions", "config")
    op.drop_column("sessions", "test")
