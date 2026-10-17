"""Tokens: one table for every workspace API token, with scopes (`ingest`, `runtime`, `onboarding:write`), expiry
and revocation. Each workspace's ingest token moves here with scopes [ingest, runtime] (it was also the runtime
token), and keeps working; `ingest_tokens` is dropped.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Typed, so dates come back as datetimes on SQLite too.
_ingest_tokens = sa.table(
    "ingest_tokens", sa.column("workspace", sa.String()), sa.column("token_hash", sa.String()), sa.column("prefix", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    tokens = op.create_table(
        "tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("import_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_tokens_workspace", "tokens", ["workspace"])
    op.create_index("ix_tokens_import_id", "tokens", ["import_id"])

    conn = op.get_bind()
    old = conn.execute(sa.select(_ingest_tokens)).mappings().all()
    if old:
        op.bulk_insert(
            tokens,
            [
                {"id": str(uuid.uuid4()), "workspace": r["workspace"], "name": "Ingest token", "token_hash": r["token_hash"], "prefix": r["prefix"],
                 "scopes": ["ingest", "runtime"], "import_id": None, "created_by": None, "created_at": r["created_at"], "expires_at": None, "revoked_at": None}
                for r in old
            ],
        )
    op.drop_table("ingest_tokens")


def downgrade() -> None:
    ingest = op.create_table(
        "ingest_tokens",
        sa.Column("workspace", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace"),
        sa.UniqueConstraint("token_hash"),
    )
    # Back to one token per workspace: its newest live token with the ingest scope.
    conn = op.get_bind()
    t = sa.table(
        "tokens", sa.column("workspace", sa.String()), sa.column("token_hash", sa.String()), sa.column("prefix", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("scopes", sa.JSON()), sa.column("revoked_at", sa.DateTime(timezone=True)),
    )
    rows = conn.execute(sa.select(t).where(t.c.revoked_at.is_(None)).order_by(t.c.created_at)).mappings().all()
    keep: dict[str, dict] = {}
    for r in rows:
        if "ingest" in (r["scopes"] or []):
            keep[r["workspace"]] = {k: r[k] for k in ("workspace", "token_hash", "prefix", "created_at")}
    if keep:
        op.bulk_insert(ingest, list(keep.values()))
    op.drop_index("ix_tokens_import_id", table_name="tokens")
    op.drop_index("ix_tokens_workspace", table_name="tokens")
    op.drop_table("tokens")
