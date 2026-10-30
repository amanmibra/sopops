"""Sessions with chosen tags aren't checked (`workspaces.analysis_skip_tags`): a `legacy` agent's calls come in and show,
but sopops doesn't flag them for rules from prompts they don't use yet.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("analysis_skip_tags", JSONDoc, nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("analysis_skip_tags")
