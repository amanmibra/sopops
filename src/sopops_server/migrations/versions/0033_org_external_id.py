"""The team a login provider owns, mirrored here (`orgs.external_id`).

A provider that owns the teams reports them on every sign-in, and sopops matches them on the provider's
own org id, kept as `<provider>:<their org id>` so two providers can't collide. Every team made in sopops keeps it
NULL, which is what marks it as sopops's own: mirroring never renames, deletes or un-joins one of those.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("external_id", sa.String(256), nullable=True))
    op.create_index("ix_orgs_external_id", "orgs", ["external_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orgs_external_id", table_name="orgs")
    with op.batch_alter_table("orgs") as batch:
        batch.drop_column("external_id")
