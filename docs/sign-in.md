# Sign in

With no login provider set up, there's no sign-in, which suits local use and demos. Set up GitHub, Google or email, or set `SOPOPS_AUTH_REQUIRED=true` for passwords alone, and the UI and API need sign-in. Agents, webhooks and coding agents keep using workspace tokens and webhook secrets.

## Turn on sign-in with passwords

Set `SOPOPS_AUTH_REQUIRED=true` and restart. No email setup needed. Passwords work whenever sign-in is on.

On a fresh install, the first person sets up their name, sign-in email, password and team name. They own the team and its workspace. Then come a join link, the team's platforms (LiveKit, ElevenLabs, Vapi or other; change in Settings → Agents and platforms), agents, the first release and calls. These can wait; the home page keeps a "Get set up" list.

- Passwords: 10 to 256 characters, not the email. Stored as argon2id hashes.
- Wrong tries: 10 per address and 30 per client per 15 minutes.
- A wrong password and an unknown address get the same message.

## Add people

- **Join link:** Settings → Members → Share a join link.
- **Invite:** an owner or admin adds emails in Settings → Members as Admin or Member.
- **Email domain:** if the team allows their domain, they just sign in.

"New here? Sign up" on the sign-in page tells people to get the join link from their team. Others see "Ask your team for its join link".

Invites:

- Existing accounts join right away. Others open the link and set a password, or sign in with that email another way.
- With SMTP, sopops emails the link. You can always copy it.
- Links last 7 days and are stored hashed. Under "Invited, not signed in yet", cancel, resend (with email) or copy a new link (without). A new link replaces the old one, which stops working.
- An invite can't set a password for an existing account.

## Teams and roles

The first person to sign in on a fresh install owns the existing team, with every workspace. Each team has one workspace. Make or switch teams from the account menu.

Only verified emails count. One verified email is one person across GitHub, Google and email links.

| Role | Can |
|---|---|
| Owner | Everything, including managing owners |
| Admin | Add and remove people, change roles, set email domains |
| Member | Everything else |

Nobody can remove or demote the last owner.

## Reset a password

An owner or admin clicks "Reset password" on a row in Settings → Members and sends the link. Admins can't reset owners. If the only owner is locked out, run on the server:

```sh
sopops reset-password ana@example.com   # add --url https://sopops.example.com without SOPOPS_PUBLIC_URL
```

- Reset links work once, for 1 hour.
- With email, "Forgot password?" sends the link.
- Change your own under your avatar → Change password.
- Setting or changing a password signs that person out everywhere else.

## Open sign-up to anyone

`SOPOPS_OPEN_SIGNUP=true` (with sign-in on) lets anyone sign up at `/sign-up` and own a new team, with the same setup plus how sopops builds instructions. Existing addresses are told to sign in. Addresses with an open invite, or at a team's member domain, are turned away. Tries count against the password limits.

> **Warning:** sopops doesn't check that people own the email they sign up with, and every team runs on this server's model keys. Turn it on only when sopops is open to the public.

## Sign in with email

Set `SOPOPS_SMTP_HOST` and `SOPOPS_SMTP_FROM`, plus `SOPOPS_SMTP_PORT`, `SOPOPS_SMTP_USER` and `SOPOPS_SMTP_PASSWORD` as needed. The sign-in page gets "Email me a sign-in link instead" and "Forgot password?". Add `SOPOPS_ALLOWED_EMAIL_DOMAINS` to let people at those domains make accounts through an emailed set-password link.

- Links work once, for 15 minutes. Limit: 5 per address and 20 per client per 15 minutes.
- The code sits after `#`, which browsers never send to the server, and the page needs one click, so mail scanners that open links can't use it up.
- The page always says "If that address can sign in, we sent a link".

`SOPOPS_EMAIL_DEV=1` turns on email sign-in with no SMTP, for demos. Links go to the server log at INFO, marked `[SOPOPS_EMAIL_DEV]`.

> **Warning:** Never use `SOPOPS_EMAIL_DEV` on a reachable server. Anyone who reads the log can sign in as anyone.

## Sign in with GitHub

Create an OAuth app under GitHub → Settings (yours or an organization's) → Developer settings → OAuth Apps.

- Homepage URL: e.g. `https://sopops.example.com`
- Callback URL: `https://sopops.example.com/v1/auth/callback/github` (locally `http://127.0.0.1:8484/v1/auth/callback/github`)
- Make a client secret. Set `SOPOPS_GITHUB_OAUTH_CLIENT_ID` and `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET`.

A GitHub App's client id and secret also work. Add the callback URL to its "Callback URL" list. Scopes are `read:user` and `user:email`. sopops uses the primary email only if GitHub verified it. For GitHub Enterprise Server, also set `SOPOPS_GITHUB_URL` and `SOPOPS_GITHUB_API_URL` ([Configuration](configuration.md)).

## Sign in with Google

1. Google Cloud → APIs & Services → OAuth consent screen, with `openid`, `email` and `profile` scopes.
2. Credentials → Create credentials → OAuth client ID → Web application.
3. Redirect URI: `https://sopops.example.com/v1/auth/callback/google` (locally `http://127.0.0.1:8484/v1/auth/callback/google`).
4. Set `SOPOPS_GOOGLE_CLIENT_ID` and `SOPOPS_GOOGLE_CLIENT_SECRET`.

Only addresses Google marks `email_verified` count.

## Run behind a proxy

- Set `SOPOPS_PUBLIC_URL` (e.g. `https://sopops.example.com`) so callback, sign-in and invite links match what you registered.
- Run uvicorn with `--proxy-headers` and `--forwarded-allow-ips`. It then sees `X-Forwarded-Proto` for Secure cookies, and the per-client email limit counts each real client, so the proxy's own address doesn't use up the limit.

## Security

- GitHub and Google use OAuth with `state` and PKCE.
- Email, invite and password links are random tokens, stored hashed.
- Sessions are random tokens in an HttpOnly, SameSite=Lax cookie, stored hashed. They last 30 days from the last visit and end on sign out.
- Cookie requests that change something send `X-CSRF-Token`, the `sopops_csrf` cookie's value. Other sites can't read it. The web UI does this.
- With sign-in on, fetching instructions needs the workspace's runtime token or a signed-in person, even with no token yet. Make one in Settings → Access.
- Every route's rule is in [`src/sopops_server/auth/access.py`](../src/sopops_server/auth/access.py).

## Add a login provider

Providers register into `sopops.auth.providers`. A package can add one (SAML, say) through the `sopops.login_providers` entry point group. One that replaces passwords can call `providers.unregister("password")`.
