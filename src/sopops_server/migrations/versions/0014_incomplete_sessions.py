"""Sessions: the status `partial` (missing turns, or the sender said the transcript is incomplete) is now `incomplete`,
so it isn't confused with the procedure result `partial`, which is unchanged.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-18
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE sessions SET status = 'incomplete' WHERE status = 'partial'")


def downgrade() -> None:
    op.execute("UPDATE sessions SET status = 'partial' WHERE status = 'incomplete'")
