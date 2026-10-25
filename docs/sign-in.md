# Sign in

sopops ships four ways to sign in, and runs with none of them until one is set up, which suits local use and demos.

| Way | Set | Notes |
|---|---|---|
| Password | `SOPOPS_AUTH_REQUIRED=true` | Works with no email set up. |
| Email sign-in link | `SOPOPS_SMTP_*` | A link that works once, for 15 minutes. |
| GitHub | `SOPOPS_GITHUB_OAUTH_CLIENT_ID`, `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET` | OAuth with `state` and PKCE. |
| Google | `SOPOPS_GOOGLE_CLIENT_ID`, `SOPOPS_GOOGLE_CLIENT_SECRET` | OAuth with `state` and PKCE. |

Set up GitHub, Google or email and sign-in is on. Passwords are offered whenever it is on, so `SOPOPS_AUTH_REQUIRED=true` alone is a sopops people sign in to with passwords and nothing else. Agents, webhooks and coding agents keep using workspace tokens and webhook secrets.

## Turn on sign-in with passwords

Set `SOPOPS_AUTH_REQUIRED=true` and restart. The first person then sets up their name, email, password and team name, and owns the team and its workspace.

| | |
|---|---|
| Passwords | 10 to 256 characters, not the email, stored as argon2id hashes |
| A wrong password | The same message as an unknown address |
| Tries | 10 per address and 30 per client IP per 15 minutes |

The rest (a join link, the team's platforms, agents, the first release, calls) can wait; the home page keeps a "Get set up" list.

## Add people

| Way | How |
|---|---|
| Join link | Settings, Members, Share a join link. |
| Invite | An owner or admin adds emails in Settings, Members, as Admin or Member. |
| Email domain | If the team allows their domain, they just sign in. |

- Existing accounts join right away. Others open the link and set a password, or sign in with that email another way.
- With SMTP, sopops emails the link. You can always copy it.
- A new link replaces the old one, which stops working.
- Under "Invited, not signed in yet", cancel, resend (with email) or copy a new link (without).
- An invite can't set a password for an existing account.

## Teams and roles

Each team has one workspace, and you make or switch teams from the account menu.

| Role | Can |
|---|---|
| Owner | Everything, including managing owners |
| Admin | Add and remove people, change roles, set email domains |
| Member | Everything else |

Only verified emails count, and one verified email is one person across every provider. Nobody can remove or demote the last owner.

## Reset a password

An owner or admin clicks "Reset password" on a row in Settings, Members, and sends the link; admins can't reset owners. If the only owner is locked out, run on the server:

```sh
sopops reset-password ana@example.com   # add --url https://sopops.example.com without SOPOPS_PUBLIC_URL
```

- With email set up, "Forgot password?" sends the link.
- Change your own under your avatar, Change password.
- Setting or changing a password signs that person out everywhere else.

## Open sign-up to anyone

`SOPOPS_OPEN_SIGNUP=true`, with sign-in on, lets anyone sign up at `/sign-up` and own a new team.

- Existing addresses are told to sign in.
- Addresses with an open invite, or at a team's member domain, are turned away.
- Tries count against the password limits.

> **Warning:** sopops doesn't check that people own the email they sign up with, and every team runs on this server's model keys. Turn it on only when sopops is open to the public.

## Sign in with email

Set the `SOPOPS_SMTP_*` variables ([Configuration](configuration.md#sign-in-and-email)). The sign-in page then offers "Email me a sign-in link instead" and "Forgot password?".

- The token sits after `#`, which browsers never send to the server, and the page needs one click, so mail scanners that open links can't use one up.
- The page always says "If that address can sign in, we sent a link".
- `SOPOPS_ALLOWED_EMAIL_DOMAINS` lets people at those domains make accounts through an emailed set-password link.
- `SOPOPS_EMAIL_DEV=1` turns on email sign-in with no SMTP, for demos. Links go to the server log at INFO, marked `[SOPOPS_EMAIL_DEV]`.

> **Warning:** Never use `SOPOPS_EMAIL_DEV` on a reachable server. Anyone who reads the log can sign in as anyone.

## Sign in with GitHub

Create an OAuth app under GitHub, Settings (yours or an organization's), Developer settings, OAuth Apps.

- Homepage URL: e.g. `https://sopops.example.com`
- Callback URL: `https://sopops.example.com/v1/auth/callback/github` (locally `http://127.0.0.1:8484/v1/auth/callback/github`)
- Make a client secret, then set `SOPOPS_GITHUB_OAUTH_CLIENT_ID` and `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET`.
- A GitHub App's client id and secret also work. Add the callback URL to its "Callback URL" list.

Scopes are `read:user` and `user:email`, and sopops uses the primary email only if GitHub verified it. For GitHub Enterprise Server, also set `SOPOPS_GITHUB_URL` and `SOPOPS_GITHUB_API_URL` ([Configuration](configuration.md)).

## Sign in with Google

1. Google Cloud, APIs & Services, OAuth consent screen, with `openid`, `email` and `profile` scopes.
2. Credentials, Create credentials, OAuth client ID, Web application.
3. Redirect URI: `https://sopops.example.com/v1/auth/callback/google` (locally `http://127.0.0.1:8484/v1/auth/callback/google`).
4. Set `SOPOPS_GOOGLE_CLIENT_ID` and `SOPOPS_GOOGLE_CLIENT_SECRET`.

Only addresses Google marks `email_verified` count.

## Choose which ones appear

`SOPOPS_LOGIN_PROVIDERS` is a comma-separated list of provider ids, and the only ways in this sopops then offers.

```sh
SOPOPS_LOGIN_PROVIDERS=github,password   # these two, whatever else is set up
```

| | |
|---|---|
| Ids | `github`, `google`, `email`, `password`, and any a plugin registers |
| Unset | Every provider that's set up is offered. The default. |
| An id that isn't set up | Skipped, as it is when the list is unset. |
| A list that names nothing set up | Ignored whole, with a warning in the log, so a typo can't lock everyone out. |

A provider the list leaves out is gone everywhere: off the sign-in page, and `/v1/auth/login/<id>` answers 404.

## Add your own provider

A package registers a provider through the `sopops.login_providers` entry point, and sopops loads that group at startup. Nothing in sopops names it, and nothing in sopops changes.

```python
# acme_sso/__init__.py
import os

from sopops_server.auth.providers import (LoginProvider, Profile, ProviderError, authorization_url, fetch_token,
                                          get_json)

BASE = "https://sso.acme.example"


class AcmeSSO(LoginProvider):
    id = "acme-sso"       # the id in /v1/auth/login/acme-sso and /v1/auth/callback/acme-sso
    label = "Acme SSO"    # what the button says

    def configured(self) -> bool:
        """Whether this deployment has what the provider needs. False keeps it off the sign-in page."""
        return bool(os.environ.get("ACME_CLIENT_ID") and os.environ.get("ACME_CLIENT_SECRET"))

    def authorize_url(self, redirect_uri: str, state: str, code_verifier: str) -> str:
        """Where the browser goes. `authorization_url` adds `state` and a PKCE S256 challenge."""
        return authorization_url(f"{BASE}/oauth/authorize", os.environ["ACME_CLIENT_ID"], "openid email profile",
                                 redirect_uri, state, code_verifier)

    def profile(self, code: str, redirect_uri: str, code_verifier: str) -> Profile:
        """The code, traded for who signed in. Raise ProviderError and the person reads its message."""
        token = fetch_token(f"{BASE}/oauth/token", os.environ["ACME_CLIENT_ID"], os.environ["ACME_CLIENT_SECRET"],
                            code, redirect_uri, code_verifier, self.label)
        u = get_json(f"{BASE}/oauth/userinfo", token, self.label)
        if not u.get("email_verified"):
            raise ProviderError(f"{self.label} hasn't verified that address")
        # provider_user_id is the id that survives somebody changing their address.
        return Profile(provider=self.id, provider_user_id=str(u["sub"]), email=u["email"].strip().lower(),
                       name=u.get("name", ""), avatar_url=u.get("picture"))
```

```toml
# pyproject.toml
[project.entry-points."sopops.login_providers"]
acme-sso = "acme_sso:AcmeSSO"
```

Install the package next to sopops and restart. The button appears once `configured()` is true, and `SOPOPS_LOGIN_PROVIDERS=acme-sso` makes it the only way in. A provider that mails a link instead of redirecting sets `redirects = False` and implements `configured` alone, as `email` does.

A provider can bring more than a name and an address:

| Also | Set | What sopops does with it |
|---|---|---|
| Its own teams | `owns_teams = True`, and fill `Profile.orgs` | Mirrors them on every sign-in. Its team ids match `orgs.external_id`, its roles map onto owner, admin and member, and a team made in sopops is never renamed, deleted or taken away on its say-so. |
| Making teams and invites | `manages_teams()`, `create_team`, `invite_to_team`, `team_invites`, `revoke_team_invite`, `set_team_role` | sopops' own screens name a team and invite a colleague, through the provider's API. Without them, `new_team_url` sends people to the provider's own page. |
| Its own sign-out | `end_session(refresh_token)`, and fill `Profile.refresh_token` | Keeps that token encrypted on the login session (`SOPOPS_SECRET_KEY`) and spends it on sign-out, so the next click asks who they are. Signing out never fails because of it. |
| Its own webhooks | `webhook_events`, `webhook_actions`, `webhook_secret_env` | Takes Svix-signed deliveries at `POST /v1/auth/webhooks/<id>`, stores every one, and ends that person's sessions here when the provider says they can't sign in there any more. |

PropelAuth is one such provider, in a package of its own outside this repo.

## Run behind a proxy

- Set `SOPOPS_PUBLIC_URL` (e.g. `https://sopops.example.com`) so callback, sign-in and invite links match what you registered.
- Run uvicorn with `--proxy-headers` and `--forwarded-allow-ips`. It then sees `X-Forwarded-Proto` for Secure cookies, and the per-client email limit counts each real client.

## How long links last

Every link below is a random token, stored hashed.

| Link or try | Lasts | Limit |
|---|---|---|
| Email sign-in link | Once, 15 minutes | 5 per address and 20 per client per 15 minutes |
| Password reset link | Once, 1 hour | |
| Invite link | 7 days | |
| Wrong password tries | | 10 per address and 30 per client per 15 minutes |
| Session cookie | 30 days from the last visit, and ends on sign out | |

## Security

- Every provider that redirects uses OAuth with `state` and PKCE.
- Sessions are random tokens in an HttpOnly, SameSite=Lax cookie, stored hashed.
- Cookie requests that change something send `X-CSRF-Token`, the `sopops_csrf` cookie's value. Other sites can't read it. The web UI does this.
- A `sopops_signed_in=1` cookie (Path=/, not HttpOnly) is set and cleared beside the session, so pages on the same host, such as a landing page, can show "Open app" instead of "Sign in". It's a hint only and grants nothing.
- With sign-in on, fetching instructions needs the workspace's runtime token or a signed-in person, even with no token yet. Make one in Settings, Access.
- Every route's rule is in [`src/sopops_server/auth/access.py`](../src/sopops_server/auth/access.py).
