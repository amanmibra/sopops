"""A step's OR options are rule results of their own (`rule_results.kind` "option"), with their place among the step's
options (`option`) and the step's rule id (`option_of`). Null on every other rule.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        batch.add_column(sa.Column("option", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("option_of", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        batch.drop_column("option_of")
        batch.drop_column("option")
