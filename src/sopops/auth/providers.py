"""Login providers: a registry that `github`, `google` and `email` register into, and that a plugin can add to.

A redirect provider (GitHub, Google, most plugins) sends the browser to its sign-in page (`authorize_url`, with `state` and a PKCE code challenge) and, when
the browser comes back with a code, trades it for the person's profile (`profile`). Only a verified email counts: a
provider returns `email=None` when it has none, and sign-in is refused. `email` doesn't redirect (`redirects = False`):
it mails a link instead (`sopops.auth.email`), so the login and callback routes don't take it.

**Plugins.** A package adds providers by registering them, either from code (`register(MyProvider())`) or through the
`sopops.login_providers` entry point group, whose entries are loaded when the app starts (`load_plugins`). That's the
extension point for, say, SAML in a private package; the open core ships GitHub and Google.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points

import httpx
from authlib.oauth2.client import OAuth2Client

log = logging.getLogger("sopops.auth")

ENTRY_POINTS = "sopops.login_providers"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class Profile:
    """Who signed in, as the provider says. `email`: a verified address, lowercased, or None."""

    provider: str
    provider_user_id: str
    email: str | None
    name: str = ""
    avatar_url: str | None = None


class ProviderError(Exception):
    """The provider refused the code or couldn't be reached. The message is safe to show."""


class LoginProvider:
    """One way to sign in. Subclasses set `id` and `label` and implement the three methods (a provider that doesn't
    redirect, like `email`, sets `redirects = False` and implements only `configured`)."""

    id: str = ""
    label: str = ""
    redirects: bool = True

    def configured(self) -> bool:
        """Whether this sopops has what it needs (a client id and secret, say) to offer it."""
        raise NotImplementedError

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        """Where to send the browser to sign in. It comes back to `redirect_uri` with `code` and `state`."""
        raise NotImplementedError

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        """Trade the code for the profile. Raises ProviderError."""
        raise NotImplementedError


def authorization_url(endpoint: str, client_id: str, scope: str, redirect_uri: str, state: str, code_verifier: str, **extra: str) -> str:
    """The provider's sign-in URL with `state` and a PKCE (S256) code challenge, built by Authlib."""
    client = OAuth2Client(None, client_id=client_id, scope=scope, redirect_uri=redirect_uri, code_challenge_method="S256")
    url, _ = client.create_authorization_url(endpoint, state=state, code_verifier=code_verifier, **extra)
    return url


def fetch_token(endpoint: str, client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str, who: str) -> str:
    """Trade the authorization code (with the PKCE code verifier) for an access token. Raises ProviderError."""
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier,
            "client_id": client_id, "client_secret": client_secret}
    try:
        res = httpx.post(endpoint, data=data, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        got = res.json()
    except (httpx.HTTPError, ValueError):
        raise ProviderError(f"{who} couldn't be reached to finish signing in") from None
    if not isinstance(got, dict) or not got.get("access_token"):  # GitHub answers 200 with {"error": ...} for a bad code
        error = got.get("error") if isinstance(got, dict) else None
        raise ProviderError(f"{who} didn't accept the sign-in ({error or f'HTTP {res.status_code}'})")
    return got["access_token"]


def get_json(url: str, access_token: str, who: str, accept: str = "application/json") -> object:
    try:
        res = httpx.get(url, headers={"Authorization": f"Bearer {access_token}", "Accept": accept}, timeout=TIMEOUT)
    except httpx.HTTPError:
        raise ProviderError(f"{who} couldn't be reached to finish signing in") from None
    if res.status_code != 200:
        raise ProviderError(f"{who} didn't share the profile (HTTP {res.status_code})")
    return res.json()


_registry: dict[str, LoginProvider] = {}


def register(provider: LoginProvider) -> LoginProvider:
    """Add a provider, or replace the one with its id."""
    if not provider.id:
        raise ValueError("a login provider needs an id")
    _registry[provider.id] = provider
    return provider


def unregister(id: str) -> None:
    _registry.pop(id, None)


def get(id: str) -> LoginProvider | None:
    return _registry.get(id)


def registered() -> list[LoginProvider]:
    return list(_registry.values())


def configured() -> list[LoginProvider]:
    """The providers people can sign in with here."""
    return [p for p in _registry.values() if p.configured()]


def load_plugins() -> None:
    """Load the `sopops.login_providers` entry points. Each one registers its providers when loaded (an entry that
    loads to a LoginProvider subclass or instance is registered here)."""
    for ep in entry_points(group=ENTRY_POINTS):
        try:
            got = ep.load()
        except Exception:
            log.exception("couldn't load the login provider plugin %s", ep.name)
            continue
        if isinstance(got, type) and issubclass(got, LoginProvider):
            register(got())
        elif isinstance(got, LoginProvider):
            register(got)
