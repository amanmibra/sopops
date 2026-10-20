"""The `prompt_texts` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class PromptText(SQLModel, table=True):
    """A prompt a session reported in its config (`prompt`), stored once per workspace: the config keeps its hash."""

    __tablename__ = "prompt_texts"

    workspace_id: str = Field(primary_key=True, max_length=128)
    hash: str = Field(primary_key=True, max_length=16)
    text: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
