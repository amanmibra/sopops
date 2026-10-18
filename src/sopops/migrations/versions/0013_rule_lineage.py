"""Rule lineage: which rule a reworded rule replaced (`sopops.lineage`), backfilled for existing releases.

The backfill is `sopops.lineage.rebuild` for every workspace (what `sopops lineage rebuild` runs), with rule patterns
following it. It uses sopops's ORM, so it runs after the last migration (`after_upgrade`, see `migrations/env.py`),
against the schema the code expects. It needs the sopc binary (the one the Store was given, else SOPC_BIN or `sopc` on
PATH) to read old releases; it runs in a savepoint, so if it can't, the table is still created and a warning says to
run the command.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-18
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlmodel import Session

from sopops.lineage import rebuild_all
from sopops.patterns import rekey
from sopops.sopc import Sopc

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("sopops.migrations")


def upgrade() -> None:
    op.create_table(
        "rule_lineage",
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("procedure_id", sa.String(256), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("previous_rule_id", sa.String(64), nullable=True),
        sa.Column("lineage_id", sa.String(64), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("how", sa.String(16), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "procedure_id", "rule_id"),
    )
    op.create_index("ix_rule_lineage_lineage", "rule_lineage", ["workspace_id", "procedure_id", "lineage_id"])
    op.create_index("ix_rule_lineage_previous", "rule_lineage", ["workspace_id", "procedure_id", "previous_rule_id"])
    op.get_context().config.attributes.setdefault("after_upgrade", []).append(backfill)


def backfill(connection: sa.Connection, sopc: Sopc | None) -> None:
    sopc = sopc or Sopc()
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        try:
            done = rebuild_all(db, sopc, follow=rekey)
            db.commit()
        except Exception as e:  # sopc missing or failing: lineage starts with the next publish
            db.rollback()
            log.warning("rule lineage wasn't backfilled (%s); run `sopops lineage rebuild --workspace WS` for each workspace", e)
            return
    if any(w["links"] for w in done):
        log.info("rule lineage backfilled: %s", ", ".join(f"{w['workspace']}: {w['links']} links" for w in done))


def downgrade() -> None:
    op.drop_index("ix_rule_lineage_previous", table_name="rule_lineage")
    op.drop_index("ix_rule_lineage_lineage", table_name="rule_lineage")
    op.drop_table("rule_lineage")
