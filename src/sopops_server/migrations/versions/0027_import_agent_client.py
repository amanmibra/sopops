"""The coding agent on an agent import: the MCP client's name and version, and when it first reached sopops with the
onboarding token (`imports.agent_client`, `agent_client_version`, `agent_connected_at`).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("agent_client", sa.String(128), nullable=True))
    op.add_column("imports", sa.Column("agent_client_version", sa.String(64), nullable=True))
    op.add_column("imports", sa.Column("agent_connected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("imports", "agent_connected_at")
    op.drop_column("imports", "agent_client_version")
    op.drop_column("imports", "agent_client")
