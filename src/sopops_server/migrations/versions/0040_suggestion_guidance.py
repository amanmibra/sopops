"""What the reviewer asked a suggested fix to do (`suggestions.guidance`), sent to the model with the verdict.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.add_column(sa.Column("guidance", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.drop_column("guidance")
