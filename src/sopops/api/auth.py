"""Sign-in (`/v1/auth`), and the org you're working in with its members and invites (`/v1/org`).

The browser signs in at `/v1/auth/login/<provider>`, which sends it to GitHub or Google with `state` and a PKCE code
challenge, kept with the code verifier in a short-lived HttpOnly cookie. The provider sends it back to
`/v1/auth/callback/<provider>`, which checks `state`, trades the code for the profile and starts a login session. The
callback URL is SOPOPS_PUBLIC_URL (the address people reach sopops at) plus that path, else this request's own address.

Email sign-in: `POST /v1/auth/email` mails a link (`<sopops>/sign-in/email#<token>`) and always answers the same,
whether the address gets one or not. The page posts the token to `/v1/auth/email/confirm` when the person clicks, which
starts a login session (see `sopops.auth.email` for why the token is after `#` and needs the click).

Invite links carry their token after `#`, so it never reaches the server's access log; the page posts it here. With
SMTP set up, sopops also emails them.
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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .. import mail
from ..auth import access, email, mode, providers
from ..auth.service import INVITE_DAYS, SESSION_DAYS, Accounts, Current, LinkInvalid, Refused
from ..orm import Org
from ..schemas.auth import (
    AddPeopleIn,
    AuthState,
    EmailLinkConfirmIn,
    EmailLinkIn,
    EmailLinkSent,
    InviteCreated,
    InviteIn,
    InviteInfo,
    InviteOut,
    InviteTokenIn,
    MemberOut,
    OrgIn,
    OrgOut,
    PersonAdded,
    RoleIn,
    SignedIn,
    SwitchOrgIn,
)
from ..tokens import Forbidden, Unauthorized

log = logging.getLogger("sopops.auth")

PUBLIC_URL_ENV = "SOPOPS_PUBLIC_URL"
OAUTH_COOKIE = "sopops_oauth"
OAUTH_COOKIE_PATH = "/v1/auth/callback"
OAUTH_TTL_S = 600
SESSION_MAX_AGE = SESSION_DAYS * 24 * 3600
LINK_SENT = "If that address can sign in, we sent a link. It works for 15 minutes."


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
        found = [{"id": p.id, "label": p.label, "redirects": p.redirects} for p in providers.configured()]
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
        if p is None or not p.configured() or not p.redirects:
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
        if p is None or not p.configured() or not p.redirects:
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

    @r.post("/v1/auth/email", response_model=EmailLinkSent, status_code=202, responses={404: {"description": "Email sign-in isn't set up."}})
    def request_email_link(req: EmailLinkIn, request: Request, background: BackgroundTasks) -> dict:
        """Email a sign-in link. The answer is the same whether or not the address gets one (this sopops doesn't take
        its domain, or it asked too often: 5 links per address and 20 per client per 15 minutes)."""
        if not email.EmailLogin().configured():
            raise HTTPException(404, "sign-in by email isn't set up on this sopops")
        try:
            token = accounts.request_link(req.email, request.client.host if request.client else None, safe_next(req.next))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if token is not None:
            to, site = req.email.strip().lower(), public_url(request)
            link = email.link_url(site, token)
            background.add_task(email.deliver, email.sign_in_message(to, link, site), link)
        return {"message": LINK_SENT}

    @r.post("/v1/auth/email/confirm", response_model=SignedIn, responses={404: {"description": "Unknown, used or expired."}})
    def confirm_email_link(req: EmailLinkConfirmIn, request: Request, response: Response) -> dict:
        """Sign in with an emailed link's token: the link is used up, and a login session starts."""
        if not email.EmailLogin().configured():
            raise HTTPException(404, "sign-in by email isn't set up on this sopops")
        try:
            user, token, next = accounts.use_link(req.token)
        except LinkInvalid as e:
            raise HTTPException(404, f"{e}. Ask for a new one") from None
        except Refused as e:
            raise HTTPException(403, str(e)) from None
        log.info("user %s signed in with email", user.id)
        access.set_session_cookies(response, request, token, SESSION_MAX_AGE)
        return {**state(accounts.current(token)), "next": safe_next(next)}

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
    def lookup_invite(req: InviteTokenIn, request: Request) -> dict:
        """What an invite link is for: the org, and the email it's for. Once used, it answers only whoever used it."""
        cur = accounts.current(request.cookies.get(access.SESSION_COOKIE)) if mode.required() else None
        return accounts.invite_info(req.token, cur.user.id if cur else None)

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
        return org_out(accounts.org(org_id), role)

    @r.patch("/v1/org", response_model=OrgOut)
    def update_org(req: OrgIn, request: Request) -> dict:
        """Rename the org, or set the email domains whose people join as members when they sign in. Owners and admins."""
        _, org_id, role = org_of(request)
        try:
            o = accounts.update_org(org_id, req.name, req.allowed_domains)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return org_out(o, role)

    def send_invite(request: Request, cur: Current, org_id: str, inv: dict, token: str) -> dict:
        """The invite with its link, emailed when sopops sends email (a failure is logged, and the link is still there to copy)."""
        link = f"{public_url(request)}/invite#{token}"
        emailed = False
        if mail.sends():
            inviter = cur.user.name or cur.user.email
            try:
                mail.send(email.invite_message(inv["email"], link, inviter, accounts.org(org_id).name, inv["role"], INVITE_DAYS))
                emailed = True
            except mail.MailError as e:
                log.warning("couldn't email the invite %s: %s", inv["id"], e)
        return {**inv, "link": link, "emailed": emailed}

    @r.get("/v1/org/members", response_model=list[MemberOut])
    def list_members(request: Request) -> list[dict]:
        _, org_id, _ = org_of(request)
        return accounts.members(org_id)

    @r.post("/v1/org/members", response_model=list[PersonAdded])
    def add_members(req: AddPeopleIn, request: Request) -> list[dict]:
        """Add people by email, as admins or members (owners can add owners). Someone with an account joins now; anyone
        else gets an invite (emailed when sopops sends email) and joins when they sign in with that email, any way."""
        cur, org_id, role = org_of(request)
        try:
            added = accounts.add_people(org_id, cur.user.id, role, req.emails, req.role)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        out = []
        for a in added:
            if a["status"] == "invited":
                out.append({"email": a["email"], "status": "invited", "invite": send_invite(request, cur, org_id, a["invite"], a["token"])})
            else:
                out.append({"email": a["email"], "status": a["status"], "user_id": a.get("user_id")})
        return out

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
        """Invite someone by email. With SMTP set up it's emailed to them; either way the link comes back to copy. It
        works for 7 days, once, and replaces their open invite."""
        cur, org_id, role = org_of(request)
        try:
            inv, token = accounts.invite(org_id, cur.user.id, role, req.email, req.role)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return send_invite(request, cur, org_id, inv, token)

    @r.post("/v1/org/invites/{id}/resend", response_model=InviteCreated)
    def resend_invite(id: str, request: Request) -> dict:
        """A new link for an open invite, good for 7 more days (the old link stops working), emailed again when sopops
        sends email."""
        cur, org_id, _ = org_of(request)
        inv, token = accounts.resend_invite(org_id, id)
        return send_invite(request, cur, org_id, inv, token)

    @r.delete("/v1/org/invites/{id}", status_code=204)
    def revoke_invite(id: str, request: Request) -> Response:
        """Withdraw an invite: its link stops working."""
        _, org_id, _ = org_of(request)
        accounts.revoke_invite(org_id, id)
        return Response(status_code=204)

    return r


def org_out(o: Org, role: str) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug, "allowed_domains": list(o.allowed_domains or []), "role": role, "sends_email": mail.sends()}


def _read_flow(cookie: str | None) -> dict | None:
    if not cookie:
        return None
    try:
        got = json.loads(base64.urlsafe_b64decode(cookie.encode()))
    except (ValueError, binascii.Error):
        return None
    return got if isinstance(got, dict) else None
