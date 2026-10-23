"""The auth mode: whether this sopops needs people to sign in.

With no login provider configured, sopops runs with no sign-in (local use, demos). With at least one (GitHub, Google,
or email, which is on with SMTP set up or SOPOPS_EMAIL_DEV), sign-in is on. Passwords don't count: they're offered
whenever sign-in is on. SOPOPS_AUTH_REQUIRED (true or false) forces it either way, and `true` alone is how a team signs
in with passwords and nothing else. Read on every request, so it follows the environment.

SOPOPS_OPEN_SIGNUP (default false), with sign-in on, lets anyone make an account with a password and a new team of
their own at /sign-up. Off, people join a team with its join link or an invite.
"""

from __future__ import annotations

import os

from . import providers

ENV = "SOPOPS_AUTH_REQUIRED"
OPEN_SIGNUP_ENV = "SOPOPS_OPEN_SIGNUP"


def required() -> bool:
    forced = os.environ.get(ENV, "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    return any(p.configured() for p in providers.registered() if p.turns_on_sign_in)


def open_signup() -> bool:
    """Whether anyone can sign up with a password and make a team.

    On wherever passwords are the way in, so a sopops someone self-hosts takes sign-ups without being configured
    to: nobody needs an invite link to make an account. SOPOPS_OPEN_SIGNUP=0 turns it off, for a deployment that
    wants invites only. A provider that owns its own sign-up (and so turns passwords off) is unaffected: sign-up
    with a password 404s there whatever this says."""
    return required() and os.environ.get(OPEN_SIGNUP_ENV, "").strip().lower() not in ("0", "false", "no", "off")
