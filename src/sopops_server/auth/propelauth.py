"""Sign in with PropelAuth (OpenID Connect, with PKCE).

For a sopops that hands sign-in to PropelAuth instead of running its own. A self-hosted sopops needs none of this:
leave the variables unset and the provider isn't offered.

Set SOPOPS_PROPELAUTH_URL to the project's auth URL (`https://xxxx.propelauthtest.com` in the free test environment,
your own domain in production), and SOPOPS_PROPELAUTH_CLIENT_ID and SOPOPS_PROPELAUTH_CLIENT_SECRET from the OAuth
Config page. Register `<sopops>/v1/auth/callback/propelauth` there as the redirect URI.

PropelAuth serves the standard endpoints under its auth URL, so this is the same shape as Google: authorize, token
(which wants the client id and secret as Basic auth), then userinfo. The email counts only when `email_verified` is
true, which PropelAuth has returned from userinfo since April 2026. Their `user_id` is the stable identifier and
doesn't change when someone changes their address, so that is what's kept.

Their organizations, roles and API keys are left alone: sopops keeps its own teams, its own roles and its own tokens.
"""

from __future__ import annotations

import os

from .providers import LoginProvider, Profile, ProviderError, authorization_url, fetch_token, get_json, register

URL_ENV = "SOPOPS_PROPELAUTH_URL"
CLIENT_ID_ENV = "SOPOPS_PROPELAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "SOPOPS_PROPELAUTH_CLIENT_SECRET"
SCOPE = "openid email profile"


def _base() -> str:
    return os.environ.get(URL_ENV, "").strip().rstrip("/")


class PropelAuthLogin(LoginProvider):
    id = "propelauth"
    label = "PropelAuth"

    def configured(self) -> bool:
        return bool(_base() and os.environ.get(CLIENT_ID_ENV, "").strip() and os.environ.get(CLIENT_SECRET_ENV, "").strip())

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        return authorization_url(f"{_base()}/propelauth/oauth/authorize", os.environ[CLIENT_ID_ENV].strip(), SCOPE, redirect_uri, state, code_verifier)

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        token = fetch_token(f"{_base()}/propelauth/oauth/token", os.environ[CLIENT_ID_ENV].strip(), os.environ[CLIENT_SECRET_ENV].strip(),
                            code, redirect_uri, code_verifier, self.label, basic=True)
        u = get_json(f"{_base()}/propelauth/oauth/userinfo", token, self.label)
        if not isinstance(u, dict):
            raise ProviderError(f"{self.label} didn't share the profile")
        # OpenID calls it `sub`; PropelAuth's own responses call the same value `user_id`.
        who = u.get("sub") or u.get("user_id")
        if not who:
            raise ProviderError(f"{self.label} didn't say who signed in")
        email = u["email"].strip().lower() if u.get("email") and u.get("email_verified") is True else None
        name = " ".join(p for p in (u.get("first_name"), u.get("last_name")) if p).strip() or (u.get("name") or "").strip()
        return Profile(provider=self.id, provider_user_id=str(who), email=email, name=name,
                       avatar_url=u.get("picture_url") or u.get("picture"))


register(PropelAuthLogin())
