"""Rule patterns are named from their rule ("Didn't read the order back"), not by index ("Step 3 missed").

Existing rule patterns with an index name are renamed here from the rule's text, without a model
(`sopops.ai.programs.rule_phrase`); the next recluster, or `sopops patterns rename`, names them with the reasoning
model. Data only: no schema change.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-18
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from sopops.ai.programs import rule_phrase

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = re.compile(r"^(Step \d+ missed|Never rule \d+ broken|Warning sign \d+ not acted on|Rule \d+)$")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, description FROM patterns WHERE kind = 'rule'")).all()
    for pid, name, description in rows:
        if INDEX_NAME.match(name or "") and (description or "").strip():
            conn.execute(sa.text("UPDATE patterns SET name = :name WHERE id = :id"), {"name": rule_phrase(description)[:256], "id": pid})


def downgrade() -> None:
    pass  # the index names aren't worth bringing back
