"""The `ai_settings` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel, Text


class AISetting(SQLModel, table=True):
    """A workspace's own AI keys and models, over the deployment's defaults (`TYPESAFE_API_KEY`, `SOPOPS_MODEL_FAST`,
    `SOPOPS_MODEL_REASONING` and the provider key variables). Each field left empty falls back to that default. Keys are
    encrypted with SOPOPS_SECRET_KEY (Fernet) and never returned; `*_hint` is the last few characters."""

    __tablename__ = "ai_settings"

    workspace: str = Field(primary_key=True, max_length=128)
    typesafe_ciphertext: str | None = Field(None, sa_column=Column(Text, nullable=True))
    typesafe_hint: str | None = Field(None, max_length=16)
    provider: str | None = Field(None, max_length=32, description="anthropic, openai, gemini or other (any LiteLLM model).")
    provider_ciphertext: str | None = Field(None, sa_column=Column(Text, nullable=True))
    provider_hint: str | None = Field(None, max_length=16)
    api_base: str | None = Field(None, max_length=1024, description="For other: an OpenAI-compatible or provider base URL.")
    fast_model: str | None = Field(None, max_length=256)
    reasoning_model: str | None = Field(None, max_length=256)
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
