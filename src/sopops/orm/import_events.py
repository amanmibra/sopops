"""The `import_events` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class ImportEvent(SQLModel, table=True):
    """A progress message: from the extraction steps here, or from the coding agent (`report_progress`)."""

    __tablename__ = "import_events"

    id: int | None = Field(default=None, primary_key=True)
    import_id: str = Field(max_length=36, index=True)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    step: str = Field(max_length=64)
    message: str = Field(sa_column=Column(Text, nullable=False))
    by: str = Field(max_length=16)  # sopops | agent
