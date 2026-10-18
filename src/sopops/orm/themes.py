"""The `themes` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text


class Theme(SQLModel, table=True):
    """A phase of a procedure's sessions (opening, taking the order, closing...) that groups its patterns. Named by a
    model; names and ids stay stable across regrouping."""

    __tablename__ = "themes"
    __table_args__ = (Index("ix_themes_workspace_procedure", "workspace_id", "procedure_id"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    procedure_id: str = Field(max_length=256)
    name: str = Field(max_length=256)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    position: int = Field(default=0, description="Order within the procedure: where in a session the phase comes.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
