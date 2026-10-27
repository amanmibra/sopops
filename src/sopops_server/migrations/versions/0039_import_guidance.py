"""How the user wants their instructions organized, for an import sopops's model extracts (`imports.guidance`).

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("guidance", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("imports", "guidance")
