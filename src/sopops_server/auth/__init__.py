"""Sign-in: users and orgs in sopops's own database, login with GitHub, Google or an emailed link (or a plugin's
provider), and which routes need a login session.

- `providers`: the login provider registry, and `github`, `google` and `email`, which register into it.
- `service`: users, identities, orgs, memberships, invites, email sign-in links and login sessions.
- `access`: the auth mode, the table of how every route is authenticated, the guard that enforces it, and CSRF.
- `actor`: who the current request acts as, for the layers below the API (authors, new workspaces' org).

Imports nothing here, so `actor` can be used from anywhere without pulling in the rest.
"""
