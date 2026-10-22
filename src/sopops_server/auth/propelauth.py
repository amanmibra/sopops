"""Sign in with PropelAuth (OpenID Connect, with PKCE).

For a sopops that hands sign-in to PropelAuth instead of running its own. A self-hosted sopops needs none of this:
leave the variables unset and the provider isn't offered.

Set SOPOPS_PROPELAUTH_URL to the project's auth URL (`https://xxxx.propelauthtest.com` in the free test environment,
your own domain in production), and SOPOPS_PROPELAUTH_CLIENT_ID and SOPOPS_PROPELAUTH_CLIENT_SECRET from the OAuth
Config page. Register `<sopops>/v1/auth/callback/propelauth` there as the redirect URI.

PropelAuth serves the standard endpoints under its auth URL, so this is the same shape as Google: authorize, token,
then userinfo. Two things differ from Google. The token call sends the client id and secret in the form
(`client_secret_post`) rather than as Basic auth: PropelAuth's guidance says to send no Authorization header when the
flow uses PKCE, and sopops always uses PKCE, while their discovery document advertises `client_secret_basic` and
`client_secret_post` but not a public client. Posting the secret satisfies both. The email counts as verified when userinfo says `email_confirmed` or `email_verified`: their
response uses the first, the second arrived in April 2026. Their `user_id` is the stable identifier and doesn't change
when someone changes their address, so that is what's kept.

**Signing out** is the third thing that differs, and the reason the token response's `refresh_token` is kept. Their
logout isn't OAuth2's: it is `POST {AUTH_URL}/api/backend/v1/logout` with `{"refresh_token": …}` as JSON, and it has
to come from the backend. It takes no `Authorization` header and no backend API key, even though the path sits under
`/api/backend/v1/` where their other backend APIs do want one: the refresh token is what authenticates the call.
(Their other logout, `/api/backend/v1/user/{id}/logout_all_sessions`, is a different endpoint and does want the key;
it kills every session a user has, which isn't what signing out of one browser means.) Without this, sopops's
sign-out ends only sopops's session: PropelAuth's stays alive, and the next click on "Sign in" comes straight back
signed in with no prompt.

`end_session` makes that call. Signing out never fails because of it: PropelAuth being unreachable, slow or unhappy
is logged and the sopops session is revoked anyway.

**Their organizations are sopops's teams** (`owns_teams`), which is the fourth difference. Their userinfo answers
with `org_id_to_org_info`: a map of org id to `{org_id, org_name, url_safe_org_name, user_role, …}`. It is in their
OAuth userinfo example rather than in the discovery document's list of claims, and it does arrive. Every sign-in
hands those orgs to `sopops_server.auth.service`, which mirrors them into sopops's own, so somebody who makes a team
in PropelAuth, or is invited to one there, lands in the matching sopops team with no join link in sight. Their role
names map onto sopops's three; their default hierarchy is Owner, Admin, Member.

Someone with no team at all goes to their hosted create-organization page, `{AUTH_URL}/create_org` (`new_team_url`),
instead of to a sopops page asking for a join link nobody here can give them. Their API keys are still left alone:
sopops keeps its own tokens.
"""

from __future__ import annotations

import base64
import logging
import os
from urllib.parse import urlencode

import httpx

from .providers import (MEMBER, ROLES, LoginProvider, Profile, ProviderError, ProviderOrg, authorization_url, fetch_tokens,
                        get_json, register)

log = logging.getLogger("sopops.auth")

URL_ENV = "SOPOPS_PROPELAUTH_URL"
CLIENT_ID_ENV = "SOPOPS_PROPELAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "SOPOPS_PROPELAUTH_CLIENT_SECRET"
SCOPE = "openid email profile"
# Signing out waits on this, so it's short: a slow PropelAuth must not hold up a sign-out that happens regardless.
LOGOUT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def _base() -> str:
    return os.environ.get(URL_ENV, "").strip().rstrip("/")


def _orgs(got: object) -> tuple[ProviderOrg, ...]:
    """Their `org_id_to_org_info` as sopops teams: `{org_id: {org_id, org_name, url_safe_org_name, user_role, …}}`.

    Anything that isn't that shape, or an entry with no org id, is skipped rather than failing a sign-in: a profile
    with no orgs costs someone a team page, a refused sign-in costs them the product."""
    if not isinstance(got, dict):
        return ()
    out = []
    for key, info in got.items():
        if not isinstance(info, dict):
            continue
        id = str(info.get("org_id") or key or "").strip()
        if not id:
            continue
        name = str(info.get("org_name") or info.get("url_safe_org_name") or "").strip() or id
        out.append(ProviderOrg(id=id, name=name[:128], role=_role(info.get("user_role"))))
    return tuple(out)


def _role(their: object) -> str:
    """Their role as one of sopops's. Their default hierarchy is Owner, Admin, Member, which lines up one for one.

    A project that renamed its roles, or added one below Member, lands here with a name sopops has never heard of,
    and that becomes `member`: the role that can do the work and change nothing about the team. Guessing higher
    would hand someone the members page on the strength of a string nobody here recognises."""
    name = str(their or "").strip().lower()
    return name if name in ROLES else MEMBER


def end_session(refresh_token: str | None) -> bool:
    """End the PropelAuth session this refresh token belongs to. True if they confirmed it.

    Never raises: whatever happens here, the caller still revokes the sopops session."""
    base = _base()
    if not refresh_token or not base:
        return False
    try:
        res = httpx.post(f"{base}/api/backend/v1/logout", json={"refresh_token": refresh_token},
                         headers={"Content-Type": "application/json"}, timeout=LOGOUT_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning("PropelAuth couldn't be reached to end the session (%s); signing out of sopops anyway", type(e).__name__)
        return False
    if res.status_code != 200:
        log.warning("PropelAuth didn't end the session (HTTP %s); signing out of sopops anyway", res.status_code)
        return False
    return True


class PropelAuthLogin(LoginProvider):
    id = "propelauth"
    label = "PropelAuth"
    owns_teams = True  # their organizations are the teams; sopops mirrors them on every sign-in

    def configured(self) -> bool:
        return bool(_base() and os.environ.get(CLIENT_ID_ENV, "").strip() and os.environ.get(CLIENT_SECRET_ENV, "").strip())

    def new_team_url(self, back_to: str | None = None) -> str | None:
        """Their hosted create-organization page. `back_to` rides along as `rt`, base64 of the URL, which is what
        their own `getCreateOrgPageUrl` does; point it at sopops's sign-in and the new team is mirrored on the way
        back in."""
        base = _base()
        if not base:
            return None
        url = f"{base}/create_org"
        if back_to:
            url += "?" + urlencode({"rt": base64.b64encode(back_to.encode()).decode()})
        return url

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        return authorization_url(f"{_base()}/propelauth/oauth/authorize", os.environ[CLIENT_ID_ENV].strip(), SCOPE, redirect_uri, state, code_verifier)

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        tokens = fetch_tokens(f"{_base()}/propelauth/oauth/token", os.environ[CLIENT_ID_ENV].strip(), os.environ[CLIENT_SECRET_ENV].strip(),
                              code, redirect_uri, code_verifier, self.label, client_auth="post")
        u = get_json(f"{_base()}/propelauth/oauth/userinfo", tokens["access_token"], self.label)
        if not isinstance(u, dict):
            raise ProviderError(f"{self.label} didn't share the profile")
        # OpenID calls it `sub`; PropelAuth's own responses call the same value `user_id`.
        who = u.get("sub") or u.get("user_id")
        if not who:
            raise ProviderError(f"{self.label} didn't say who signed in")
        # Their userinfo calls it `email_confirmed`; `email_verified` arrived in April 2026. Either counts, neither assumed.
        confirmed = u.get("email_confirmed") is True or u.get("email_verified") is True
        email = u["email"].strip().lower() if u.get("email") and confirmed else None
        name = " ".join(p for p in (u.get("first_name"), u.get("last_name")) if p).strip() or (u.get("name") or "").strip()
        # Kept so signing out can end their session too (`end_session`). It's a credential: stored encrypted, never logged.
        refresh = tokens.get("refresh_token")
        return Profile(provider=self.id, provider_user_id=str(who), email=email, name=name,
                       avatar_url=u.get("picture_url") or u.get("picture"),
                       refresh_token=str(refresh) if refresh else None,
                       orgs=_orgs(u.get("org_id_to_org_info")))


register(PropelAuthLogin())
