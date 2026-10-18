"""The `tokens` table."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import JSON, Column, DateTime, Field, Index, SQLModel


class Token(SQLModel, table=True):
    """A workspace API token, stored as a sha256 hash and shown once, when made. Scopes: `ingest` (send sessions),
    `runtime` (fetch prompts, report served calls, call-start webhooks) and `onboarding:write` (a coding agent's
    import; made by the import flow, tied to `import_id`). Revoked tokens are kept, with `revoked_at`."""

    __tablename__ = "tokens"
    __table_args__ = (Index("ix_tokens_workspace", "workspace"),)

    id: str = Field(primary_key=True, max_length=36)
    workspace: str = Field(max_length=128)
    name: str = Field(default="", max_length=128)
    token_hash: str = Field(max_length=64, unique=True)
    prefix: str = Field(max_length=32, description="The token's first characters, to tell tokens apart.")
    scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    import_id: str | None = Field(default=None, max_length=36, index=True)
    created_by: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
