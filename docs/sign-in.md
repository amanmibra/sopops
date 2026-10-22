# Sign in

With no login provider set up, there's no sign-in, which suits local use and demos. Set up GitHub, Google, PropelAuth or email, or set `SOPOPS_AUTH_REQUIRED=true` for passwords alone, and the UI and API need sign-in. Agents, webhooks and coding agents keep using workspace tokens and webhook secrets.

## Turn on sign-in with passwords

Set `SOPOPS_AUTH_REQUIRED=true` and restart. No email setup needed, and passwords work whenever sign-in is on.

On a fresh install, the first person sets up their name, sign-in email, password and team name, and owns the team and its workspace. The rest (a join link, the team's platforms, agents, the first release, calls) can wait; the home page keeps a "Get set up" list.

Passwords are 10 to 256 characters, not the email, stored as argon2id hashes. A wrong password and an unknown address get the same message.

## Add people

| Way | How |
|---|---|
| Join link | Settings → Members → Share a join link. |
| Invite | An owner or admin adds emails in Settings → Members, as Admin or Member. |
| Email domain | If the team allows their domain, they just sign in. |

The sign-in page sends people without an account to their team for a join link.

Invites:

- Existing accounts join right away. Others open the link and set a password, or sign in with that email another way.
- With SMTP, sopops emails the link. You can always copy it.
- A new link replaces the old one, which stops working.
- Under "Invited, not signed in yet", cancel, resend (with email) or copy a new link (without).
- An invite can't set a password for an existing account.

## Teams and roles

Each team has one workspace. Make or switch teams from the account menu. Only verified emails count, and one verified email is one person across GitHub, Google and email links.

| Role | Can |
|---|---|
| Owner | Everything, including managing owners |
| Admin | Add and remove people, change roles, set email domains |
| Member | Everything else |

Nobody can remove or demote the last owner.

## Reset a password

An owner or admin clicks "Reset password" on a row in Settings → Members and sends the link; admins can't reset owners. If the only owner is locked out, run on the server:

```sh
sopops reset-password ana@example.com   # add --url https://sopops.example.com without SOPOPS_PUBLIC_URL
```

- With email set up, "Forgot password?" sends the link.
- Change your own under your avatar → Change password.
- Setting or changing a password signs that person out everywhere else.

## Open sign-up to anyone

`SOPOPS_OPEN_SIGNUP=true` (with sign-in on) lets anyone sign up at `/sign-up` and own a new team, with the same setup plus how sopops builds instructions.

- Existing addresses are told to sign in.
- Addresses with an open invite, or at a team's member domain, are turned away.
- Tries count against the password limits.

> **Warning:** sopops doesn't check that people own the email they sign up with, and every team runs on this server's model keys. Turn it on only when sopops is open to the public.

## Sign in with email

Set the `SOPOPS_SMTP_*` variables ([Configuration](configuration.md#sign-in-and-email)). The sign-in page then offers "Email me a sign-in link instead" and "Forgot password?". Add `SOPOPS_ALLOWED_EMAIL_DOMAINS` to let people at those domains make accounts through an emailed set-password link.

- The code sits after `#`, which browsers never send to the server, and the page needs one click, so mail scanners that open links can't use one up.
- The page always says "If that address can sign in, we sent a link".
- `SOPOPS_EMAIL_DEV=1` turns on email sign-in with no SMTP, for demos. Links go to the server log at INFO, marked `[SOPOPS_EMAIL_DEV]`.

> **Warning:** Never use `SOPOPS_EMAIL_DEV` on a reachable server. Anyone who reads the log can sign in as anyone.

## Sign in with GitHub

Create an OAuth app under GitHub → Settings (yours or an organization's) → Developer settings → OAuth Apps.

- Homepage URL: e.g. `https://sopops.example.com`
- Callback URL: `https://sopops.example.com/v1/auth/callback/github` (locally `http://127.0.0.1:8484/v1/auth/callback/github`)
- Make a client secret, then set `SOPOPS_GITHUB_OAUTH_CLIENT_ID` and `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET`.
- A GitHub App's client id and secret also work. Add the callback URL to its "Callback URL" list.

Scopes are `read:user` and `user:email`. sopops uses the primary email only if GitHub verified it. For GitHub Enterprise Server, also set `SOPOPS_GITHUB_URL` and `SOPOPS_GITHUB_API_URL` ([Configuration](configuration.md)).

## Sign in with Google

1. Google Cloud → APIs & Services → OAuth consent screen, with `openid`, `email` and `profile` scopes.
2. Credentials → Create credentials → OAuth client ID → Web application.
3. Redirect URI: `https://sopops.example.com/v1/auth/callback/google` (locally `http://127.0.0.1:8484/v1/auth/callback/google`).
4. Set `SOPOPS_GOOGLE_CLIENT_ID` and `SOPOPS_GOOGLE_CLIENT_SECRET`.

Only addresses Google marks `email_verified` count.

## Sign in with PropelAuth

For a sopops that hands sign-in to a managed service instead of running its own. Self-hosting needs none of it.

1. In PropelAuth, make a project and note its auth URL (`https://xxxx.propelauthtest.com` while testing, your own domain in production).
2. Add `https://sopops.example.com/v1/auth/callback/propelauth` as a redirect URI.
3. OAuth Config → copy the client id and secret.
4. Set `SOPOPS_PROPELAUTH_URL`, `SOPOPS_PROPELAUTH_CLIENT_ID` and `SOPOPS_PROPELAUTH_CLIENT_SECRET`, then restart.

- Only addresses PropelAuth marks `email_verified` count.
- The identity is keyed on PropelAuth's `user_id`, which survives an address change, so nobody gets a second account.
- **PropelAuth's organizations are the teams.** Every sign-in mirrors the ones its userinfo reports: each becomes a sopops team with its own workspace, and the person joins it with the role PropelAuth gave them (Owner, Admin and Member map one for one; a role sopops doesn't know becomes `member`). Teams are matched on PropelAuth's org id, so renaming one there renames it here instead of making a second, and dropping somebody from one there drops them here on their next sign-in. A team made in sopops is left alone: never renamed, never deleted, and never taken away from anyone, because PropelAuth was never asked about it. Their API keys still go unused; workspaces and tokens stay sopops'.
- **Nobody is asked for a join link.** With PropelAuth the only way in, somebody with no team is sent to their hosted create-organization page, `{AUTH_URL}/create_org`, which brings them back here signed in and in their new team. sopops's own invites and join links stay as they are for every other provider.
- Signing out ends the PropelAuth session too. Their logout isn't OAuth2's: sopops posts the sign-in's refresh token to `{AUTH_URL}/api/backend/v1/logout`, which needs no backend API key. The refresh token is kept on the login session, encrypted with `SOPOPS_SECRET_KEY`, and spent on the way out. Set that key if you set PropelAuth up: without it the token isn't stored, and sign-out ends only the sopops session, so the next click on "Sign in" walks straight back in with no prompt. PropelAuth being unreachable never blocks a sign-out; it's logged and the sopops session ends anyway.
- A session that **ends without a sign-out** leaves PropelAuth's alone: the 30-day sliding expiry running out, a password change ending the user's other sessions, or support access being turned off. sopops stops taking the cookie, so they have to sign in again, and PropelAuth may wave them through without asking.

## Choose what's offered

`SOPOPS_LOGIN_PROVIDERS`, a comma-separated list of provider ids, is the only ways to sign in this sopops offers.

```sh
SOPOPS_LOGIN_PROVIDERS=propelauth   # PropelAuth alone, whatever else is set up
```

| | |
|---|---|
| Ids | `github`, `google`, `email`, `password`, `propelauth`, and any a plugin registers |
| Unset | Every provider that's set up is offered. The default, and what self-hosting does. |
| An id that isn't set up | Skipped, the same as today. |
| A list that names nothing set up | Ignored whole, with a warning in the log, so a typo can't lock everyone out. |

A provider the list leaves out is gone everywhere: off the sign-in page, and `/v1/auth/login/<id>` answers 404.

## Move a deployment to PropelAuth

Existing accounts need no import: sopops links a new provider to the account with the same verified email, so people land on the account they already have, with its teams and roles ([`Accounts.sign_in`](../src/sopops_server/auth/service.py)).

1. Make each person a PropelAuth user with the address their sopops account uses.
2. Set the three variables above and `SOPOPS_LOGIN_PROVIDERS=propelauth`.
3. Everyone signs in once through PropelAuth, which adds a `propelauth` identity to their existing user.

| | |
|---|---|
| The match | PropelAuth's address, lowercased, against the account's. A different address makes a second account. |
| Verified only | Without `email_verified`, sign-in is refused and nothing is linked. |
| Passwords | Still stored, no longer offered. Drop `propelauth` from the list to get them back. |

To create the PropelAuth users from sopops' own instead of inviting them, post each to their backend API:

```sh
curl -X POST https://auth.sopops.dev/api/backend/v1/migrate_user/ \
  -H "Authorization: Bearer $PROPELAUTH_API_KEY" -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "email_confirmed": true, "existing_user_id": "<the sopops user id>"}'
```

`existing_password_hash` carries an argon2id hash over, so a password keeps working on their side.

## Run behind a proxy

- Set `SOPOPS_PUBLIC_URL` (e.g. `https://sopops.example.com`) so callback, sign-in and invite links match what you registered.
- Run uvicorn with `--proxy-headers` and `--forwarded-allow-ips`. It then sees `X-Forwarded-Proto` for Secure cookies, and the per-client email limit counts each real client, so the proxy's own address doesn't use up the limit.

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

- GitHub, Google and PropelAuth use OAuth with `state` and PKCE.
- Sessions are random tokens in an HttpOnly, SameSite=Lax cookie, stored hashed.
- Cookie requests that change something send `X-CSRF-Token`, the `sopops_csrf` cookie's value. Other sites can't read it. The web UI does this.
- With sign-in on, fetching instructions needs the workspace's runtime token or a signed-in person, even with no token yet. Make one in Settings → Access.
- Every route's rule is in [`src/sopops_server/auth/access.py`](../src/sopops_server/auth/access.py).

## Add a login provider

Providers register into `sopops.auth.providers`. A package can add one (SAML, say) through the `sopops.login_providers` entry point group. One that replaces passwords can call `providers.unregister("password")`, or the deployment can leave `password` out of `SOPOPS_LOGIN_PROVIDERS`.
