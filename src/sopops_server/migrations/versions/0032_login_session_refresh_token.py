"""The sign-in provider's refresh token on the login session, encrypted (`login_sessions.refresh_ciphertext`).

PropelAuth's logout is its own endpoint and wants the refresh token posted back to it, so signing out of sopops can
end their session too. Only PropelAuth sets it; every other provider leaves it NULL.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("login_sessions") as batch:
        batch.add_column(sa.Column("refresh_ciphertext", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("login_sessions") as batch:
        batch.drop_column("refresh_ciphertext")
