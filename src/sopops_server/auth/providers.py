"""Login providers: a registry that `github`, `google`, `email` and `password` register into, and that a plugin can add to.

A redirect provider (GitHub, Google, most plugins) sends the browser to its sign-in page (`authorize_url`, with `state` and a PKCE code challenge) and, when
the browser comes back with a code, trades it for the person's profile (`profile`). Only a verified email counts: a
provider returns `email=None` when it has none, and sign-in is refused. `email` doesn't redirect (`redirects = False`):
it mails a link instead (`sopops_server.auth.email`), so the login and callback routes don't take it. Nor does `password`
(`sopops_server.auth.password`), which has its own routes.

**Plugins.** A package adds providers by registering them, either from code (`register(MyProvider())`) or through the
`sopops.login_providers` entry point group, whose entries are loaded when the app starts (`load_plugins`). That's the
extension point for, say, SAML or a managed sign-in service in a package of its own; the open core ships GitHub,
Google, email links and passwords. A plugin's provider is an ordinary one and can do more than say who signed in: it
can own the teams (`owns_teams`), end its own session when somebody signs out (`end_session`) and send webhooks
(`webhook_events`, read by `sopops_server.auth.webhooks`).

**Which ones a deployment offers.** SOPOPS_LOGIN_PROVIDERS, a comma-separated list of ids, is the deployment's
allowlist: only the ids it names are offered, however many are configured. Unset, every configured provider is
offered, which is what sopops has always done and what every self-hosted sopops keeps doing. A deployment that
hands sign-in to one provider names that one. `registered()` and `get()` both apply it, so every caller agrees:
`mode.required()` counts only allowed providers, `/v1/auth/session` lists only them, and the login and callback
routes for one left out answer 404. A list that names no configured provider is ignored, with a warning, so a typo
can't lock everyone out.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points

import httpx
from authlib.oauth2.client import OAuth2Client

log = logging.getLogger("sopops.auth")

ENTRY_POINTS = "sopops.login_providers"
ALLOWLIST_ENV = "SOPOPS_LOGIN_PROVIDERS"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# sopops's roles live here, not only in `service`, because a provider that owns the teams maps its own role names
# onto them (`ProviderOrg.role`) and so has to know what they are.
OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ROLES = (OWNER, ADMIN, MEMBER)
# What a provider that doesn't do teams says when asked to make one. It reaches the person, so it says what they can
# do about it and names nobody: whose sign-in this sopops uses is not their problem.
CANT_MANAGE = "teams aren't made or shared out from here on this sopops"


@dataclass(frozen=True)
class ProviderOrg:
    """One team the provider says this person is in. `id` is the team's id *there*, which is what sopops matches on,
    so renaming it there renames the sopops team instead of making a second one. `role` is already one of sopops's
    (`owner`, `admin`, `member`): the provider maps it, because only it knows its own role names."""

    id: str
    name: str
    role: str


@dataclass(frozen=True)
class ProviderInvite:
    """Somebody a provider that owns the teams has invited to one, who isn't in it yet. The address is what names it
    there, and what withdraws it: these invites have no id of their own. `role` is already one of sopops's."""

    email: str
    role: str
    created_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Profile:
    """Who signed in, as the provider says. `email`: a verified address, lowercased, or None.

    `refresh_token` is the provider's own refresh token, kept for a provider that needs it back to end its own
    session when someone signs out of sopops (`LoginProvider.end_session`). It is a credential: stored encrypted on
    the login session, never logged and never returned. A provider that runs no session of its own leaves it None.

    `orgs` is the teams the provider owns for this person, and only a provider with `owns_teams` fills it in. It is
    empty for GitHub, Google, email and password, so sopops's own teams, invites and join links work as they always
    have."""

    provider: str
    provider_user_id: str
    email: str | None
    name: str = ""
    avatar_url: str | None = None
    refresh_token: str | None = None
    orgs: tuple[ProviderOrg, ...] = ()


class ProviderError(Exception):
    """The provider refused the code or couldn't be reached. The message is safe to show."""


class LoginProvider:
    """One way to sign in. Subclasses set `id` and `label` and implement the three methods (a provider that doesn't
    redirect, like `email`, sets `redirects = False` and implements only `configured`). One that's offered only once
    sign-in is on for another reason, like `password`, sets `turns_on_sign_in = False`.

    A provider can also run a session of its own and end it when somebody signs out (`end_session`), and send
    webhooks (`webhook_events`, `webhook_actions`, `webhook_secret_env`). Both default to nothing, so a provider
    that only says who signed in implements neither.

    **A provider that owns the teams** (`owns_teams = True`) sets `Profile.orgs` on every sign-in and sopops mirrors
    them: its teams are the teams, and nobody joins one from the sopops side. If it can also make them and invite
    people through its API (`manages_teams`), sopops's own screens do both; if it can't, it says where someone with
    no team goes to make one (`new_team_url`), so sopops never has to know whose provider it is."""

    id: str = ""
    label: str = ""
    redirects: bool = True
    turns_on_sign_in: bool = True
    owns_teams: bool = False

    def configured(self) -> bool:
        """Whether this sopops has what it needs (a client id and secret, say) to offer it."""
        raise NotImplementedError

    def new_team_url(self, back_to: str | None = None) -> str | None:
        """Where someone with no team goes to make one, for a provider that owns the teams. `back_to` is a sopops URL
        to come back to afterwards, which a provider passes on if its page takes one. None when it has no such page:
        then sopops says where teams come from rather than offering a link that goes nowhere."""
        return None

    # --- managing the teams, for a provider that owns them -------------------------------
    #
    # A provider that owns the teams can also let sopops make them and hand them out through its API, and then
    # sopops's own screens do it: nobody is sent to somebody else's pages to name a team or invite a colleague.
    # It is a capability, not a duty. The default is "can't", so GitHub, Google, email and password are untouched,
    # and so is a provider that owns the teams but hasn't been given the credentials its API wants: there
    # `new_team_url` is still the answer, and `manages_teams` says so.
    #
    # Every one of these is a call to somebody else's server. They raise ProviderError, whose message is written to
    # be shown as it is, and the caller shows it rather than a stack trace.

    def manages_teams(self) -> bool:
        """Whether sopops can make this provider's teams, and invite people to them, from its own screens."""
        return False

    def create_team(self, provider_user_id: str, name: str) -> ProviderOrg:
        """Make a team at the provider, owned by this person (their user id *there*, not sopops's). Returns the team
        as the provider now has it, ready for sopops to mirror: its id there, its name and their role in it."""
        raise ProviderError(CANT_MANAGE)

    def invite_to_team(self, team_id: str, email: str, role: str) -> None:
        """Invite somebody to the team (`team_id` is the team's id *there*). The provider emails them; whoever
        accepts is in the team there, and in the sopops team that mirrors it the moment they sign in."""
        raise ProviderError(CANT_MANAGE)

    def team_invites(self, team_id: str) -> list[ProviderInvite]:
        """The invites still waiting at the provider for this team."""
        raise ProviderError(CANT_MANAGE)

    def revoke_team_invite(self, team_id: str, email: str) -> None:
        """Withdraw the invite waiting there for this address."""
        raise ProviderError(CANT_MANAGE)

    def set_team_role(self, team_id: str, provider_user_id: str, role: str) -> None:
        """Change somebody's role in the team there, so the change survives their next sign-in (which mirrors the
        provider's roles over sopops's)."""
        raise ProviderError(CANT_MANAGE)

    # --- a session of the provider's own -------------------------------------------------

    def end_session(self, refresh_token: str) -> bool:
        """End the session this person has at the provider, because they signed out of sopops. True if it ended.

        Only a provider that runs its own session needs this, and only one that set `Profile.refresh_token` at
        sign-in gets a token to end it with. Signing out of sopops never fails because of it: the caller revokes
        the sopops session whatever happens here, so an implementation logs its trouble and returns False rather
        than raising."""
        return False

    # --- webhooks ------------------------------------------------------------------------
    #
    # A provider that sends webhooks says which it can send and which of them mean something here, and the route
    # (`sopops_server.api.provider_webhooks`) stores every delivery either way. The values of `webhook_actions` are
    # sopops's own verbs (`sopops_server.auth.webhooks.END_SESSIONS` and the rest), never the provider's strings.

    webhook_events: tuple[str, ...] = ()
    webhook_actions: dict[str, str] = {}
    webhook_secret_env: str = ""

    def sends_webhooks(self) -> bool:
        """Whether `/v1/auth/webhooks/<id>` is open for this provider at all. False answers 404 there."""
        return bool(self.webhook_events or self.webhook_secret_env)

    def webhook_secret(self) -> str | None:
        """The endpoint secret its deliveries are signed with, from the environment. None when none is set up, and
        then the route answers 503 rather than taking unsigned traffic."""
        return (os.environ.get(self.webhook_secret_env, "").strip() or None) if self.webhook_secret_env else None

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

    Most providers want only `access_token`, and `fetch_token` hands them that. A provider that ends its own
    session on the way out needs the `refresh_token` the same response carries, so it gets the whole dict and keeps
    what it needs.

    `client_auth` is how the client proves itself: `post` sends the id and secret in the form (GitHub, Google),
    `basic` sends them as HTTP Basic (`client_secret_basic`), and `none` sends only the id, for a provider whose PKCE
    flow refuses client authentication, as some do."""
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


def known(id: str) -> LoginProvider | None:
    """The provider with this id whether or not the deployment offers it. Signing out is the caller: a session
    started before SOPOPS_LOGIN_PROVIDERS changed still has to be ended with the provider that started it."""
    return _registry.get(id)


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
