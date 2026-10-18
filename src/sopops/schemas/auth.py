"""Sign-in, the org, its members and invites."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel

Role = Literal["owner", "admin", "member"]


class ProviderOut(BaseModel):
    id: str = Field(description="`github`, `google`, or a plugin's.")
    label: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None


class OrgSummary(BaseModel):
    id: str
    name: str
    slug: str
    role: Role = Field(description="Your role in it.")


class AuthState(BaseModel):
    required: bool = Field(description="Whether this sopops needs people to sign in. False: no sign-in at all.")
    providers: list[ProviderOut] = Field(description="What people can sign in with.")
    user: UserOut | None = Field(description="Who's signed in, if anyone.")
    org: OrgSummary | None = Field(description="The org you're working in; none until someone adds you to one.")
    orgs: list[OrgSummary] = Field(description="Every org you're in.")


class SwitchOrgIn(BaseModel):
    org_id: str


class OrgOut(BaseModel):
    id: str
    name: str
    slug: str
    allowed_domains: list[str] = Field(description="People with a verified email at these domains join as members when they sign in.")
    role: Role = Field(description="Your role in it.")


class OrgIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    allowed_domains: list[str] | None = Field(None, max_length=50, description="Email domains, like example.com.")


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    avatar_url: str | None
    role: Role
    joined_at: datetime
    last_login_at: datetime | None


class RoleIn(BaseModel):
    role: Role


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    role: Role = "member"


class InviteOut(BaseModel):
    id: str
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime


class InviteCreated(InviteOut):
    link: str = Field(description="The invite link, shown once. Whoever signs in with the invited email and opens it joins.")


class InviteTokenIn(BaseModel):
    token: str = Field(max_length=256, description="From the invite link (the part after #).")


class InviteInfo(BaseModel):
    org_id: str
    org_name: str
    email: str = Field(description="Who it's for: they must sign in with this email.")
    role: Role
    expires_at: datetime
    accepted: bool = Field(description="You used it already: you're in the org.")
