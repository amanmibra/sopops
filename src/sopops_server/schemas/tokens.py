"""Workspace API tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel

TokenScope = Literal["ingest", "runtime", "config", "onboarding:write"]


class TokenIn(BaseModel):
    name: str = Field("", max_length=128, description="What it's for, e.g. 'LiveKit agents'.")
    scopes: list[Literal["ingest", "runtime", "config"]] = Field(
        default_factory=lambda: ["ingest", "runtime"], min_length=1,
        description="`ingest`: send sessions. `runtime`: fetch instructions, report served calls, call-start webhooks. `config`: read and edit the forms (agents, shared instructions, procedures, groups, settings) as drafts; it can't publish.",
    )
    expires_in_days: int | None = Field(None, ge=1, le=3650, description="Default: never expires.")


class TokenOut(BaseModel):
    id: str
    workspace: str
    name: str
    prefix: str = Field(description="The token's first characters, to tell tokens apart.")
    scopes: list[TokenScope]
    import_id: str | None = Field(description="Onboarding tokens: the import they belong to.")
    created_by: str | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool = Field(description="Neither revoked nor expired.")


class TokenCreated(TokenOut):
    token: str = Field(description="Shown once. Send as `Authorization: Bearer <token>`.")
