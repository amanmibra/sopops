"""Every webhook a login provider sends, stored (`provider_events`).

sopops subscribes to every event a login provider can send and keeps each delivery: the provider's name, its event
type as sent, its message id (unique per provider, so a retry is handled once), the whole payload, when it arrived
and what, if anything, sopops did about it. Rows are append-only.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONDoc = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "provider_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("message_id", sa.String(128), nullable=False),
        sa.Column("payload", JSONDoc, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("provider", "message_id", name="uq_provider_events_message"),
    )
    op.create_index("ix_provider_events_received", "provider_events", ["received_at"])
    op.create_index("ix_provider_events_type", "provider_events", ["provider", "event_type", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_events_type", table_name="provider_events")
    op.drop_index("ix_provider_events_received", table_name="provider_events")
    op.drop_table("provider_events")
