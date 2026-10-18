"""Sign-in (`/v1/auth`), and the org you're working in with its members and invites (`/v1/org`).

The browser signs in at `/v1/auth/login/<provider>`, which sends it to GitHub or Google with `state` and a PKCE code
challenge, kept with the code verifier in a short-lived HttpOnly cookie. The provider sends it back to
`/v1/auth/callback/<provider>`, which checks `state`, trades the code for the profile and starts a login session. The
callback URL is SOPOPS_PUBLIC_URL (the address people reach sopops at) plus that path, else this request's own address.

Invite links carry their token after `#`, so it never reaches the server's access log; the page posts it here.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from ..auth import access, mode, providers
from ..auth.service import SESSION_DAYS, Accounts, Current, Refused
from ..schemas.auth import (
    AuthState,
    InviteCreated,
    InviteIn,
    InviteInfo,
    InviteOut,
    InviteTokenIn,
    MemberOut,
    OrgIn,
    OrgOut,
    RoleIn,
    SwitchOrgIn,
)
from ..tokens import Forbidden, Unauthorized

log = logging.getLogger("sopops.auth")

PUBLIC_URL_ENV = "SOPOPS_PUBLIC_URL"
OAUTH_COOKIE = "sopops_oauth"
OAUTH_COOKIE_PATH = "/v1/auth/callback"
OAUTH_TTL_S = 600
SESSION_MAX_AGE = SESSION_DAYS * 24 * 3600


def public_url(request: Request) -> str:
    """Where people reach sopops: SOPOPS_PUBLIC_URL, else this request's own scheme and host."""
    return (os.environ.get(PUBLIC_URL_ENV, "").strip() or str(request.base_url)).rstrip("/")


def callback_url(request: Request, provider: str) -> str:
    return f"{public_url(request)}/v1/auth/callback/{provider}"


def safe_next(next: str | None) -> str:
    """A path on this site to go back to after signing in; anything else (another site) is home."""
    if not next or not next.startswith("/") or next.startswith("//") or "\\" in next:
        return "/"
    return next


def router(accounts: Accounts) -> APIRouter:
    r = APIRouter(tags=["auth"])

    def login(request: Request) -> Current:
        cur = access.signed_in(request)
        if cur is None:
            if not mode.required():
                raise HTTPException(409, "sign-in is off on this sopops: there are no members to manage")
            raise Unauthorized("sign in first")
        return cur

    def org_of(request: Request) -> tuple[Current, str, str]:
        """The signed-in user, the org they're working in, and their role there."""
        cur = login(request)
        if cur.org_id is None:
            raise Forbidden(access.NO_MEMBERSHIP)
        return cur, cur.org_id, cur.memberships[cur.org_id]

    def state(cur: Current | None) -> dict:
        found = [{"id": p.id, "label": p.label} for p in providers.configured()]
        if cur is None:
            return {"required": mode.required(), "providers": found, "user": None, "org": None, "orgs": []}
        orgs = [{"id": o.id, "name": o.name, "slug": o.slug, "role": cur.memberships[o.id]} for o in accounts.orgs(list(cur.memberships))]
        u = cur.user
        return {"required": True, "providers": found, "user": {"id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url},
                "org": next((o for o in orgs if o["id"] == cur.org_id), None), "orgs": orgs}

    # --- signing in -----------------------------------------------------------------

    @r.get("/v1/auth/session", response_model=AuthState)
    def auth_session(request: Request, response: Response) -> dict:
        """Whether sign-in is on, how to sign in, and who's signed in. Signed in, it also renews the cookies for
        another 30 days (sessions slide)."""
        if not mode.required():
            return state(None)
        token = request.cookies.get(access.SESSION_COOKIE)
        cur = accounts.current(token)
        if cur is not None and token:
            access.set_session_cookies(response, request, token, SESSION_MAX_AGE)
        return state(cur)

    @r.get("/v1/auth/login/{provider}", response_class=RedirectResponse, status_code=302, responses={404: {"description": "No such provider here."}})
    def sign_in(provider: str, request: Request, next: str | None = None) -> Response:
        """Start signing in: redirects to the provider."""
        p = providers.get(provider)
        if p is None or not p.configured():
            raise HTTPException(404, f"sign-in with '{provider}' isn't set up on this sopops")
        st, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        out = RedirectResponse(p.authorize_url(callback_url(request, provider), st, verifier), status_code=302)
        flow = json.dumps({"p": provider, "s": st, "v": verifier, "n": safe_next(next)}).encode()
        out.set_cookie(OAUTH_COOKIE, base64.urlsafe_b64encode(flow).decode(), max_age=OAUTH_TTL_S, path=OAUTH_COOKIE_PATH, httponly=True,
                       samesite="lax", secure=access.secure(request))
        return out

    @r.get("/v1/auth/callback/{provider}", include_in_schema=False)
    def auth_callback(provider: str, request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> Response:
        """The provider sends the browser back here. On success: a login session, then the page it started from."""

        def fail(message: str) -> Response:
            out = RedirectResponse(f"/sign-in?error={quote(message)}", status_code=303)
            out.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
            return out

        flow = _read_flow(request.cookies.get(OAUTH_COOKIE))
        p = providers.get(provider)
        if p is None or not p.configured():
            return fail(f"sign-in with '{provider}' isn't set up on this sopops")
        if error:
            return fail("you cancelled signing in" if error == "access_denied" else f"{p.label} didn't sign you in ({error[:64]})")
        if flow is None or flow.get("p") != provider or not state or not hmac.compare_digest(str(flow.get("s", "")), state) or not code:
            return fail("that sign-in link expired or didn't start here. Try again")
        try:
            profile = p.profile(code, callback_url(request, provider), str(flow.get("v", "")))
            user, token = accounts.sign_in(profile)
        except providers.ProviderError as e:
            return fail(str(e))
        except Refused as e:
            return fail(str(e))
        log.info("user %s signed in with %s", user.id, provider)
        out = RedirectResponse(safe_next(flow.get("n")), status_code=303)
        out.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
        access.set_session_cookies(out, request, token, SESSION_MAX_AGE)
        return out

    @r.post("/v1/auth/logout", status_code=204)
    def sign_out(request: Request) -> Response:
        """Sign out: the login session ends at once."""
        accounts.sign_out(request.cookies.get(access.SESSION_COOKIE))
        out = Response(status_code=204)
        access.clear_session_cookies(out, request)
        return out

    @r.put("/v1/auth/org", response_model=AuthState)
    def switch_org(req: SwitchOrgIn, request: Request) -> dict:
        """Work in another org you're in."""
        cur = login(request)
        accounts.switch_org(cur, req.org_id)
        return state(accounts.current(request.cookies.get(access.SESSION_COOKIE)))

    @r.post("/v1/auth/invites/lookup", response_model=InviteInfo, responses={404: {"description": "Unknown, used, withdrawn or expired."}})
    def lookup_invite(req: InviteTokenIn) -> dict:
        """What an invite link is for: the org, and the email it's for."""
        return accounts.invite_info(req.token)

    @r.post("/v1/auth/invites/accept", response_model=AuthState, responses={403: {"description": "Signed in with another email."}})
    def accept_invite(req: InviteTokenIn, request: Request) -> dict:
        """Join the invite's org. You must be signed in with the email it's for."""
        cur = login(request)
        accounts.accept(req.token, cur)
        return state(accounts.current(request.cookies.get(access.SESSION_COOKIE)))

    # --- the org --------------------------------------------------------------------

    @r.get("/v1/org", response_model=OrgOut)
    def get_org(request: Request) -> dict:
        """The org you're working in."""
        _, org_id, role = org_of(request)
        o = accounts.org(org_id)
        return {"id": o.id, "name": o.name, "slug": o.slug, "allowed_domains": list(o.allowed_domains or []), "role": role}

    @r.patch("/v1/org", response_model=OrgOut)
    def update_org(req: OrgIn, request: Request) -> dict:
        """Rename the org, or set the email domains whose people join as members when they sign in. Owners and admins."""
        _, org_id, role = org_of(request)
        try:
            o = accounts.update_org(org_id, req.name, req.allowed_domains)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": o.id, "name": o.name, "slug": o.slug, "allowed_domains": list(o.allowed_domains or []), "role": role}

    @r.get("/v1/org/members", response_model=list[MemberOut])
    def list_members(request: Request) -> list[dict]:
        _, org_id, _ = org_of(request)
        return accounts.members(org_id)

    @r.patch("/v1/org/members/{user_id}", response_model=MemberOut)
    def set_member_role(user_id: str, req: RoleIn, request: Request) -> dict:
        """Change someone's role. Only an owner makes or changes an owner, and the org keeps at least one."""
        _, org_id, role = org_of(request)
        return accounts.set_role(org_id, role, user_id, req.role)

    @r.delete("/v1/org/members/{user_id}", status_code=204)
    def remove_member(user_id: str, request: Request) -> Response:
        """Remove someone from the org. They can still sign in, but see nothing of it."""
        _, org_id, role = org_of(request)
        accounts.remove_member(org_id, role, user_id)
        return Response(status_code=204)

    @r.get("/v1/org/invites", response_model=list[InviteOut])
    def list_invites(request: Request) -> list[dict]:
        """Invites not yet used, withdrawn or expired."""
        _, org_id, _ = org_of(request)
        return accounts.invites(org_id)

    @r.post("/v1/org/invites", response_model=InviteCreated, status_code=201)
    def create_invite(req: InviteIn, request: Request) -> dict:
        """Invite someone by email. Nothing is sent: copy the link to them. It works for 7 days, once."""
        cur, org_id, role = org_of(request)
        try:
            inv, token = accounts.invite(org_id, cur.user.id, role, req.email, req.role)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {**inv, "link": f"{public_url(request)}/invite#{token}"}

    @r.delete("/v1/org/invites/{id}", status_code=204)
    def revoke_invite(id: str, request: Request) -> Response:
        """Withdraw an invite: its link stops working."""
        _, org_id, _ = org_of(request)
        accounts.revoke_invite(org_id, id)
        return Response(status_code=204)

    return r


def _read_flow(cookie: str | None) -> dict | None:
    if not cookie:
        return None
    try:
        got = json.loads(base64.urlsafe_b64decode(cookie.encode()))
    except (ValueError, binascii.Error):
        return None
    return got if isinstance(got, dict) else None
