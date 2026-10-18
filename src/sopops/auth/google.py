"""Sign in with Google (OpenID Connect, with PKCE).

Set SOPOPS_GOOGLE_CLIENT_ID and SOPOPS_GOOGLE_CLIENT_SECRET from an OAuth client of type "Web application" in Google
Cloud; its authorized redirect URI is `<sopops>/v1/auth/callback/google`. The profile comes from Google's userinfo
endpoint with the access token, and the email counts only when `email_verified` is true.
"""

from __future__ import annotations

import os

from .providers import LoginProvider, Profile, authorization_url, fetch_token, get_json, register

CLIENT_ID_ENV = "SOPOPS_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "SOPOPS_GOOGLE_CLIENT_SECRET"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile"


class GoogleLogin(LoginProvider):
    id = "google"
    label = "Google"

    def configured(self) -> bool:
        return bool(os.environ.get(CLIENT_ID_ENV, "").strip() and os.environ.get(CLIENT_SECRET_ENV, "").strip())

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        return authorization_url(AUTHORIZE_URL, os.environ[CLIENT_ID_ENV].strip(), SCOPE, redirect_uri, state, code_verifier, prompt="select_account")

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        token = fetch_token(TOKEN_URL, os.environ[CLIENT_ID_ENV].strip(), os.environ[CLIENT_SECRET_ENV].strip(), code, redirect_uri, code_verifier,
                            self.label)
        u = get_json(USERINFO_URL, token, self.label)
        assert isinstance(u, dict)
        email = u["email"].strip().lower() if u.get("email") and u.get("email_verified") is True else None
        return Profile(provider=self.id, provider_user_id=str(u["sub"]), email=email, name=(u.get("name") or "").strip(), avatar_url=u.get("picture"))


register(GoogleLogin())
