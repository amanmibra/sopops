"""Sign in with GitHub (OAuth 2.0 with PKCE).

Set SOPOPS_GITHUB_OAUTH_CLIENT_ID and SOPOPS_GITHUB_OAUTH_CLIENT_SECRET from a GitHub OAuth app, or from a GitHub App
(its client id and a client secret: GitHub Apps can sign users in the same way). Either way the callback URL is
`<sopops>/v1/auth/callback/github`. The email is the account's primary address, and only if GitHub has verified it
(`GET /user/emails`). SOPOPS_GITHUB_URL and SOPOPS_GITHUB_API_URL point at GitHub Enterprise.
"""

from __future__ import annotations

import os

from ..github_app import web_url
from ..github_client import api_url
from .providers import LoginProvider, Profile, authorization_url, fetch_token, get_json, register

CLIENT_ID_ENV = "SOPOPS_GITHUB_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "SOPOPS_GITHUB_OAUTH_CLIENT_SECRET"
SCOPE = "read:user user:email"
ACCEPT = "application/vnd.github+json"


class GitHubLogin(LoginProvider):
    id = "github"
    label = "GitHub"

    def configured(self) -> bool:
        return bool(os.environ.get(CLIENT_ID_ENV, "").strip() and os.environ.get(CLIENT_SECRET_ENV, "").strip())

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        return authorization_url(f"{web_url()}/login/oauth/authorize", os.environ[CLIENT_ID_ENV].strip(), SCOPE, redirect_uri, state, code_verifier,
                                 allow_signup="true")

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        token = fetch_token(f"{web_url()}/login/oauth/access_token", os.environ[CLIENT_ID_ENV].strip(), os.environ[CLIENT_SECRET_ENV].strip(),
                            code, redirect_uri, code_verifier, self.label)
        user = get_json(f"{api_url()}/user", token, self.label, ACCEPT)
        emails = get_json(f"{api_url()}/user/emails", token, self.label, ACCEPT)
        assert isinstance(user, dict) and isinstance(emails, list)
        primary = next((e for e in emails if e.get("primary")), None)
        email = primary["email"].strip().lower() if primary and primary.get("verified") is True and primary.get("email") else None
        return Profile(provider=self.id, provider_user_id=str(user["id"]), email=email, name=(user.get("name") or user.get("login") or "").strip(),
                       avatar_url=user.get("avatar_url"))


register(GitHubLogin())
