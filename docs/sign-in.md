# Sign in

With no login provider set up, sopops has no sign-in: that's how local use and demos work. Set up GitHub, Google, email or any mix, or set `SOPOPS_AUTH_REQUIRED=true` to use passwords alone, and people sign in to use the UI and the API; agents, webhooks and coding agents keep using their workspace tokens and webhook secrets, with no login.

**Passwords, with no email setup.** Whenever sign-in is on, people can sign in with an email and password. A self-hosted team needs nothing else: set `SOPOPS_AUTH_REQUIRED=true` and restart. On a fresh install, sopops walks the first person through setup, one question per screen: their name, an email and password (the email is what they sign in with), and what the team is called. That account owns the team, and the team gets its workspace. Then come a join link to share, the platforms the team uses (LiveKit, ElevenLabs, Vapi, or something else; sopops shows only those, and Settings → Agents and platforms changes them), bringing in agents, the first release and connecting calls. Each of the later steps can wait, and the team's home keeps a "Get set up" list until they're done. After that, people join with the team's join link (Settings → Members → Share a join link), or an owner or admin adds them by email and each new person opens their invite link, picks a name and password, and joins. The sign-in page has "New here? Sign up", which tells people to open the join link someone on their team sent them, or ask a team owner or admin for it. Passwords are 10 to 256 characters and can't be the email address. They're stored as argon2id hashes. Each address gets 10 wrong tries and each client 30 per 15 minutes, and a wrong password says the same thing as an unknown address.

**Open sign-up.** Set `SOPOPS_OPEN_SIGNUP=true` (with sign-in on) and anyone can sign up at `/sign-up`: their name, an email and password, and what their team is called. They own that new team and its workspace, and go on through the same setup as the first person: a join link, platforms, how sopops builds an agent's instructions, agents, the first release and calls. An address that already has an account is told to sign in instead. Nobody checks that the address is theirs, so sign-up turns away an address with an open invite, or at a domain a team takes members from, and joins no team but the new one. Each try counts against the password limits above. Every team uses this server's model keys, so turn it on only when you mean to host sopops for anyone.

**Forgotten passwords.** An owner or admin clicks "Reset password" on someone's row in Settings → Members and sends them the link (admins can't reset an owner's). If the only owner is locked out, whoever runs the server prints one:

```sh
sopops reset-password ana@example.com          # --url https://sopops.example.com if SOPOPS_PUBLIC_URL isn't set
```

A reset link works once, for an hour, and setting the password signs that person out everywhere else. With email set up, the sign-in page also has "Forgot password?", which emails the link. Anyone signed in with a password can change it from the menu under their avatar (Change password), which also signs them out everywhere else.

**Who gets in.** The first person to sign in on a fresh install becomes owner of the existing org, which owns every workspace there is. In the app an org is a team, with one workspace: anyone can make another team from their account menu, and switch between their teams there. After that, people join with the team's join link, when an owner or admin adds them (Settings → Members) or, if the org allows their email domain, just by signing in. Someone who signs in and hasn't been added sees "Ask your team for its join link". Only verified email addresses count, and the same verified email is the same person whether they sign in with GitHub, Google or an email link.

**Adding people.** Enter one or more email addresses and pick Admin or Member. Someone who already has an account joins right away. Anyone else gets an invite link: they open it and set a password, or sign in with that email any other way, and they're in. With SMTP set up, sopops emails the invite; either way you get the link to copy. Invites work for 7 days. Under "Invited, not signed in yet" you can cancel one, and resend it (with email) or copy a new link (without). Links are stored hashed, so a new link replaces the old one, which stops working. An invite can't set a password for an address that already has an account: that person signs in the way they always do.

**Roles.** Owners and admins add and remove people, change roles and set the org's email domains; members do everything else. Only an owner makes, changes or removes an owner, and nobody can remove or demote the last one.

**Email.** Set `SOPOPS_SMTP_HOST` and `SOPOPS_SMTP_FROM` (plus `SOPOPS_SMTP_PORT`, `SOPOPS_SMTP_USER` and `SOPOPS_SMTP_PASSWORD` as your mail provider says) and the sign-in page gets "Email me a sign-in link instead" and "Forgot password?". With `SOPOPS_ALLOWED_EMAIL_DOMAINS` set too, people at those domains can make an account themselves: sopops emails them a link to set a password, so only someone who reads that inbox gets the account. The link works once, for 15 minutes. Its code is after `#` in the link, which browsers never send to a server, and the page it opens asks for one click to sign in, so mail scanners that open links can't use it up. Each address can get 5 links per 15 minutes, and each client 20. The page always says "If that address can sign in, we sent a link", so it never tells anyone which addresses have accounts.

For a demo or local use, `SOPOPS_EMAIL_DEV=1` turns email sign-in on with no SMTP: each link is written to the server log at INFO, marked `[SOPOPS_EMAIL_DEV]`, instead of being sent. Don't use it on a server other people can reach: anyone who reads the log can sign in as anyone.

**GitHub.** Make an OAuth app: GitHub → Settings → Developer settings → OAuth Apps → New OAuth App (or the same under an organization's settings).

- Homepage URL: where people reach sopops, e.g. `https://sopops.example.com`.
- Authorization callback URL: `https://sopops.example.com/v1/auth/callback/github` (locally, `http://127.0.0.1:8484/v1/auth/callback/github`).
- Make a client secret, then set `SOPOPS_GITHUB_OAUTH_CLIENT_ID` and `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET`.

A GitHub App's client id and a client secret made for it work in the same two variables, if you'd rather not make a second app: add the same callback URL to the App's "Callback URL" list. sopops asks for `read:user` and `user:email` and uses the account's primary email, only if GitHub has verified it. GitHub Enterprise Server: also set `SOPOPS_GITHUB_URL` and `SOPOPS_GITHUB_API_URL` ([Configuration](configuration.md)).

**Google.** In Google Cloud: APIs & Services → OAuth consent screen (set it up, with the `openid`, `email` and `profile` scopes), then Credentials → Create credentials → OAuth client ID → Web application.

- Authorized redirect URI: `https://sopops.example.com/v1/auth/callback/google` (locally, `http://127.0.0.1:8484/v1/auth/callback/google`).
- Set `SOPOPS_GOOGLE_CLIENT_ID` and `SOPOPS_GOOGLE_CLIENT_SECRET`.

Only addresses Google marks `email_verified` count.

**Behind a proxy**, set `SOPOPS_PUBLIC_URL` to the address people use (`https://sopops.example.com`), so the callback URLs, sign-in links and invite links match what you registered. The per-client limit on email links counts the address the request comes from, so behind a proxy it counts the proxy unless uvicorn trusts its forwarded headers. Cookies are marked Secure when the request is https (with uvicorn behind a proxy, pass `--proxy-headers` and `--forwarded-allow-ips` so it sees `X-Forwarded-Proto`).

**How it works.** GitHub and Google sign-in use OAuth with `state` and PKCE; email, invite and password links are random tokens stored hashed. A login session is a random token in an HttpOnly, SameSite=Lax cookie, stored hashed; it lasts 30 days from the last visit and ends when you sign out. Requests that change something with the cookie also send `X-CSRF-Token`, the value of the `sopops_csrf` cookie (the web UI does this), which another site can't read. With sign-in on, fetching instructions needs the workspace's runtime token (or someone signed in), even for a workspace that has none yet, so make one in Settings → Access first. Every route's rule is in one table, `src/sopops/auth/access.py`.

**More login providers.** Providers register into `sopops.auth.providers`; a package can add one (SAML, say) through the `sopops.login_providers` entry point group. One that signs people in some other way can also turn passwords off with `providers.unregister("password")`.
