"""The `model_calls` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, Index, SQLModel


class ModelCall(SQLModel, table=True):
    """One call to a model (not a cache hit): which model, the tokens it reported and what it cost by the price table
    in use, attributed to a workspace, the session it was about (when it was about one) and what it was for. Prompts
    and replies are never stored. See `sopops_server.ai.spend`."""

    __tablename__ = "model_calls"
    __table_args__ = (
        Index("ix_model_calls_workspace_created", "workspace_id", "created_at"),
        Index("ix_model_calls_session", "session_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: str = Field(max_length=128)
    session_id: str | None = Field(default=None, max_length=36)
    purpose: str = Field(max_length=16)  # route | check | review | cluster | theme | suggestion | calibrate | onboarding
    model: str = Field(max_length=256)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = Field(default=None, description="By the price table in use when it was made; null when the model has no price.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
