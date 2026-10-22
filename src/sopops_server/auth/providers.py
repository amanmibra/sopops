"""Login providers: a registry that `github`, `google`, `email` and `password` register into, and that a plugin can add to.

A redirect provider (GitHub, Google, most plugins) sends the browser to its sign-in page (`authorize_url`, with `state` and a PKCE code challenge) and, when
the browser comes back with a code, trades it for the person's profile (`profile`). Only a verified email counts: a
provider returns `email=None` when it has none, and sign-in is refused. `email` doesn't redirect (`redirects = False`):
it mails a link instead (`sopops_server.auth.email`), so the login and callback routes don't take it. Nor does `password`
(`sopops_server.auth.password`), which has its own routes.

**Plugins.** A package adds providers by registering them, either from code (`register(MyProvider())`) or through the
`sopops.login_providers` entry point group, whose entries are loaded when the app starts (`load_plugins`). That's the
extension point for, say, SAML in a private package; the open core ships GitHub and Google.

**Which ones a deployment offers.** SOPOPS_LOGIN_PROVIDERS, a comma-separated list of ids, is the deployment's
allowlist: only the ids it names are offered, however many are configured. Unset, every configured provider is
offered, which is what sopops has always done and what every self-hosted sopops keeps doing. The hosted sopops sets
it to `propelauth` and offers that alone. `registered()` and `get()` both apply it, so every caller agrees:
`mode.required()` counts only allowed providers, `/v1/auth/session` lists only them, and the login and callback
routes for one left out answer 404. A list that names no configured provider is ignored, with a warning, so a typo
can't lock everyone out.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib.metadata import entry_points

import httpx
from authlib.oauth2.client import OAuth2Client

log = logging.getLogger("sopops.auth")

ENTRY_POINTS = "sopops.login_providers"
ALLOWLIST_ENV = "SOPOPS_LOGIN_PROVIDERS"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class Profile:
    """Who signed in, as the provider says. `email`: a verified address, lowercased, or None.

    `refresh_token` is the provider's own refresh token, for the one provider that needs it back to end its session
    when someone signs out of sopops (PropelAuth; see `sopops_server.auth.propelauth`). It is a credential: stored
    encrypted on the login session, never logged and never returned. Every other provider leaves it None."""

    provider: str
    provider_user_id: str
    email: str | None
    name: str = ""
    avatar_url: str | None = None
    refresh_token: str | None = None


class ProviderError(Exception):
    """The provider refused the code or couldn't be reached. The message is safe to show."""


class LoginProvider:
    """One way to sign in. Subclasses set `id` and `label` and implement the three methods (a provider that doesn't
    redirect, like `email`, sets `redirects = False` and implements only `configured`). One that's offered only once
    sign-in is on for another reason, like `password`, sets `turns_on_sign_in = False`."""

    id: str = ""
    label: str = ""
    redirects: bool = True
    turns_on_sign_in: bool = True

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


def fetch_tokens(endpoint: str, client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str, who: str,
                 client_auth: str = "post") -> dict:
    """Trade the authorization code (with the PKCE code verifier) for the whole token response. Raises ProviderError.

    Most providers want only `access_token`, and `fetch_token` hands them that. PropelAuth also needs the
    `refresh_token` the same response carries, because ending its session on the way out means posting that token
    back to it, so it gets the dict and keeps what it needs.

    `client_auth` is how the client proves itself: `post` sends the id and secret in the form (GitHub, Google),
    `basic` sends them as HTTP Basic (`client_secret_basic`), and `none` sends only the id, for a provider whose PKCE
    flow refuses client authentication (PropelAuth documents this: with PKCE, send no Authorization header)."""
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier,
            "client_id": client_id}
    auth = None
    if client_auth == "basic":
        auth = (client_id, client_secret)
    elif client_auth == "post":
        data["client_secret"] = client_secret
    try:
        res = httpx.post(endpoint, data=data, auth=auth, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        got = res.json()
    except (httpx.HTTPError, ValueError):
        raise ProviderError(f"{who} couldn't be reached to finish signing in") from None
    if not isinstance(got, dict) or not got.get("access_token"):  # GitHub answers 200 with {"error": ...} for a bad code
        error = got.get("error") if isinstance(got, dict) else None
        raise ProviderError(f"{who} didn't accept the sign-in ({error or f'HTTP {res.status_code}'})")
    return got


def fetch_token(endpoint: str, client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str, who: str,
                client_auth: str = "post") -> str:
    """`fetch_tokens`, for the providers that want the access token and nothing else."""
    return fetch_tokens(endpoint, client_id, client_secret, code, redirect_uri, code_verifier, who, client_auth)["access_token"]


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


_warned_about = ""


def allowlist() -> set[str] | None:
    """The ids SOPOPS_LOGIN_PROVIDERS names, or None when every registered provider is offered.

    None is also the answer to a list that names nothing configured here: offering nothing would leave no way in, so
    a typo falls back to the behaviour of an unset list and says so in the log, once per value."""
    global _warned_about
    raw = os.environ.get(ALLOWLIST_ENV, "")
    names = {n.strip().lower() for n in raw.split(",")} - {""}
    if not names:
        return None
    if any(id in names and p.configured() for id, p in _registry.items()):
        _warned_about = ""
        return names
    if _warned_about != raw:
        _warned_about = raw
        log.warning("%s=%s names no login provider that's set up here (registered: %s); offering all of them instead",
                    ALLOWLIST_ENV, raw, ", ".join(sorted(_registry)) or "none")
    return None


def get(id: str) -> LoginProvider | None:
    """The provider with this id, if this deployment offers it (SOPOPS_LOGIN_PROVIDERS)."""
    allowed = allowlist()
    if allowed is not None and id not in allowed:
        return None
    return _registry.get(id)


def registered() -> list[LoginProvider]:
    """Every provider this deployment offers, configured or not: all of them, or the ones the allowlist names."""
    allowed = allowlist()
    return [p for id, p in _registry.items() if allowed is None or id in allowed]


def configured() -> list[LoginProvider]:
    """The providers people can sign in with here."""
    return [p for p in registered() if p.configured()]


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
