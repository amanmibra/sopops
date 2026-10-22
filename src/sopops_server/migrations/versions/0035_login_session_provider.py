"""Which login provider signed a session in (`login_sessions.provider`).

Signing out ends the provider's own session too, for a provider that runs one, and that means knowing which
provider the session came from rather than guessing from the person's identities. NULL on every session signed in
before this, which signs out exactly as it did.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("login_sessions", sa.Column("provider", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("login_sessions", "provider")
