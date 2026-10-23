"""Support sessions: a staff member working in one customer's workspace to help them."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import BaseModel


class SupportStartIn(BaseModel):
    org: str = Field(description="The customer: their org id, or a workspace of theirs.", min_length=1, max_length=128)
    reason: str = Field("", description="Optional. Kept in the audit trail the customer reads, as it comes.", max_length=2000)
    minutes: int = Field(60, description="How long the session lasts. It dies then, whatever happens.", ge=1, le=480)


class SupportStarted(BaseModel):
    org_id: str
    org_name: str
    until: datetime = Field(description="When the session dies. It doesn't slide, and using it doesn't push this out.")


class SupportState(BaseModel):
    running: bool = Field(description="A support session is on: everything you see and change belongs to `org_name`.")
    staff: bool = Field(description="You're allowed to start support sessions (your address is in SOPOPS_SUPPORT_EMAILS).")
    org_id: str | None = Field(None, description="The customer org being worked in, while a session is running.")
    org_name: str | None = None
    until: datetime | None = None


class SupportSessionOut(BaseModel):
    id: str
    staff_email: str = Field(description="Who looked. Copied when the session started, so a rename doesn't lose it.")
    reason: str = Field("", description="Why, if the staff member said. Empty otherwise.")
    started_at: datetime
    ended_at: datetime | None = Field(None, description="When it was ended. None while it's running, and for one left to expire.")
    until: datetime
    requests: int = Field(description="Requests served to the session (approximate).")
    last_path: str | None = None
    running: bool


class OrgSupportOut(BaseModel):
    support_access: bool = Field(description="Whether support staff may open a session on this team's workspace. On by default.")
    sessions: list[SupportSessionOut] = Field(description="Every support session against this team, newest first.")


class OrgSupportIn(BaseModel):
    support_access: bool


class SupportTeamOut(BaseModel):
    """One team a support session can be started against, for the list staff pick from."""

    id: str
    name: str
    workspaces: list[str] = Field(default_factory=list, description="The team's workspaces, so two teams of the same name tell apart.")
    support_access: bool = Field(description="False means this team has turned support access off and can't be picked.")
