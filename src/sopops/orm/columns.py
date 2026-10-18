"""Column types shared by tables."""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# JSONB on Postgres (indexable, compact), JSON elsewhere.
JSONDoc = JSON().with_variant(JSONB(), "postgresql")
