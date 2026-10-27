"""Sign-in, the org, its members and invites."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel

Role = Literal["owner", "admin", "member"]
Platform = Literal["livekit", "elevenlabs", "vapi"]
PlatformPick = Literal["livekit", "elevenlabs", "vapi", "other"]


class ProviderOut(BaseModel):
    id: str = Field(description="`github`, `google`, `password`, or a plugin's.")
    label: str
    redirects: bool = Field(description="Signing in goes through `/v1/auth/login/<id>`. False for `password` (post to `/v1/auth/password/login`).")
    owns_teams: bool = Field(False, description="This provider keeps the teams: sopops mirrors the ones it reports on every sign-in, and nobody "
                             "joins a team from the sopops side. False for every provider that leaves teams to sopops.")
    new_team_url: str | None = Field(None, description="Where somebody with no team goes to make one, for a provider that owns the teams: its own "
                                     "page, which signs them back in here afterwards. None when it has no such page.")


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None
    has_password: bool = Field(description="They can sign in with a password, so they can change it.")


class OrgSummary(BaseModel):
    id: str
    name: str
    slug: str
    role: Role = Field(description="Your role in it.")
    workspace: str | None = Field(None, description="The team's workspace (the one changed most recently, if it has several). None until it has one.")


class PlatformsOut(BaseModel):
    selected: list[PlatformPick] | None = Field(description="What the team picked. `other`: something else, or not sure yet. None: nobody has "
                                                "picked yet.")
    shown: list[Platform] = Field(description="The platforms the app shows: the ones picked, or all of them when none of them was picked.")


class PlatformsIn(BaseModel):
    platforms: list[PlatformPick] = Field(min_length=1, max_length=4)


class AuthState(BaseModel):
    required: bool = Field(description="Whether this sopops needs people to sign in. False: no sign-in at all.")
    providers: list[ProviderOut] = Field(description="What people can sign in with.")
    user: UserOut | None = Field(description="Who's signed in, if anyone.")
    org: OrgSummary | None = Field(description="The org you're working in; none until someone adds you to one.")
    orgs: list[OrgSummary] = Field(description="Every org you're in.")
    first_account: bool = Field(False, description="Nobody has an account yet: post to `/v1/auth/password/first-account` to make the first one, which owns the org.")
    open_signup: bool = Field(False, description="SOPOPS_OPEN_SIGNUP is on: anyone can post to `/v1/auth/sign-up` to make an account with a "
                                                 "password and a new team they own.")
    demo_workspace: str | None = Field(None, description="SOPOPS_DEMO_WORKSPACE: a workspace anyone signed in can look at (and take the tour in), and "
                                                         "nobody outside its org can change. None: there's no demo.")
    platforms: PlatformsOut | None = Field(None, description="The platforms the org you're working in uses (with sign-in off, the first org's). "
                                           "None when you're not signed in, or in no org.")


class SignedIn(AuthState):
    next: str = Field(description="The page to open now, a path on this site.")


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
    emailed: bool = Field(description="The provider that owns the team emailed the invite itself, and `link` is empty. sopops never sends "
                                      "email: for its own invites this is false, and you send the link.")


class AddPeopleIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=100, description="Email addresses.")
    role: Role = "member"


class PersonAdded(BaseModel):
    email: str
    status: Literal["added", "invited", "already"] = Field(
        description="`added`: they have an account and joined now. `invited`: they join when they sign in with this email. `already`: a member.")
    user_id: str | None = None
    invite: InviteCreated | None = Field(None, description="For `invited`: the invite, its link (shown once), to send them.")


class InviteTokenIn(BaseModel):
    token: str = Field(max_length=256, description="From the invite link (the part after #).")


class InviteInfo(BaseModel):
    org_id: str
    org_name: str
    email: str = Field(description="Who it's for: they must sign in with this email.")
    role: Role
    expires_at: datetime
    accepted: bool = Field(description="You used it already: you're in the org.")
    has_account: bool = Field(False, description="The email has an account here: sign in to join. False: join by making one with a password.")


class PasswordLoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    next: str | None = Field(None, max_length=512, description="A path on this site to open after signing in.")


class FirstAccountIn(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field("", max_length=256)
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")


class SignUpIn(BaseModel):
    name: str = Field("", max_length=256)
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")
    team: str = Field(min_length=1, max_length=128, description="What the new team is called.")


class InviteJoinIn(BaseModel):
    token: str = Field(max_length=256, description="From the invite link (the part after #).")
    name: str = Field("", max_length=256)
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")


class PasswordLinkTokenIn(BaseModel):
    token: str = Field(max_length=256, description="From the link (the part after #).")


class PasswordLinkInfo(BaseModel):
    email: str
    expires_at: datetime


class PasswordSetIn(BaseModel):
    token: str = Field(max_length=256, description="From the link (the part after #).")
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=1024)
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")


class PasswordLinkCreated(BaseModel):
    email: str
    link: str = Field(description="Shown once. It sets their password, works once, for an hour, and ends their login sessions.")
    expires_at: datetime


class SetUpIn(BaseModel):
    team: str = Field(min_length=1, max_length=128, description="What the team is called. The org takes this name.")


class SetUpOut(BaseModel):
    org: OrgOut
    workspace: str = Field(description="The team's workspace: made now, named after the team, unless it had one.")


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="What the team is called.")


class JoinLinkIn(BaseModel):
    role: Literal["admin", "member"] = "member"
    days: int = Field(7, ge=1, le=90, description="How long it works.")


class JoinLinkOut(BaseModel):
    id: str
    role: Role
    joined: int = Field(description="How many people joined with it.")
    created_at: datetime
    expires_at: datetime


class JoinLinkCreated(JoinLinkOut):
    link: str = Field(description="The join link, shown once. Anyone who opens it can join the team with its role, until it expires or is turned off.")


class JoinTokenIn(BaseModel):
    token: str = Field(max_length=256, description="From the join link (the part after #).")


class JoinInfo(BaseModel):
    org_id: str
    org_name: str
    role: Role = Field(description="The role people join with.")
    expires_at: datetime
    member: bool = Field(description="You're in this team already.")
    needs_platforms: bool = Field(description="Nobody in the team has picked its platforms yet: whoever joins next is asked.")


class JoinIn(BaseModel):
    token: str = Field(max_length=256, description="From the join link (the part after #).")
    name: str = Field("", max_length=256)
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024, description="10 to 256 characters, not the email address.")
