"""The `calibrations` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, DateTime, Field, SQLModel

from .columns import JSONDoc


class Calibration(SQLModel, table=True):
    """Fitted decision parameters for one workspace's program (`route`, `check:<procedure id>`), with a hash of each
    output's question so an edited rule falls back to the defaults. See `sopops.ai.calibration`."""

    __tablename__ = "calibrations"

    workspace_id: str = Field(primary_key=True, max_length=128)
    program: str = Field(primary_key=True, max_length=300)
    params: dict[str, Any] = Field(sa_column=Column(JSONDoc, nullable=False))
    questions: dict[str, str] = Field(sa_column=Column(JSONDoc, nullable=False))
    model: str | None = Field(default=None, max_length=256)
    report: dict[str, Any] = Field(sa_column=Column(JSONDoc, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
