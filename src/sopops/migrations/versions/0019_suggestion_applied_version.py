"""Suggestions: the file version a draft edit wrote when applied, so a release has the edit even once later edits
built on it. Existing applied drafts get the version their apply wrote: the last version of the file written by a
suggestion at or before `applied_at`, which is stamped just after the edit.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.add_column(sa.Column("applied_version", sa.Integer(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, workspace_id, path, applied_at FROM suggestions "
                                "WHERE applied_at IS NOT NULL AND pr_url IS NULL AND path IS NOT NULL")).all()
    for sid, ws, path, applied_at in rows:
        version = conn.execute(sa.text("SELECT MAX(version) FROM file_versions WHERE workspace = :ws AND path = :path "
                                       "AND created_at <= :at AND note LIKE 'Suggestion for%'"),
                               {"ws": ws, "path": path, "at": applied_at}).scalar()
        if version is not None:
            conn.execute(sa.text("UPDATE suggestions SET applied_version = :v WHERE id = :id"), {"v": version, "id": sid})


def downgrade() -> None:
    with op.batch_alter_table("suggestions") as batch:
        batch.drop_column("applied_version")
