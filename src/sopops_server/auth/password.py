"""Sign in with an email address and a password. Always offered while sign-in is on, but it doesn't turn sign-in on by
itself (`turns_on_sign_in = False`), so a sopops with no login provider set up still runs with no sign-in. Set
SOPOPS_AUTH_REQUIRED=true to use passwords alone. A hosted sopops that signs people in some other
way can `providers.unregister("password")`, and the password routes answer 404.

Passwords are stored as argon2id hashes on the user's `password` identity. They're 10 to 256 characters and can't be
the email address. Signing in with an unknown address still checks the password against a dummy hash, so the answer
takes about as long either way.

A password is only ever set for an address someone vouched for: the first account on a fresh install, an invite (only
for an address with no account yet), a link an owner or admin made for a member, or `sopops reset-password`. So a
password can't be used to claim someone else's email, which GitHub and Google sign-in link to by address. Join links and open sign-up (SOPOPS_OPEN_SIGNUP) are the exceptions: anyone with
the link, or anyone at all, picks the address. Sign-up refuses an address an invite, a team's email domains or the
support staff list is waiting for, and a join link refuses a support address too, so an address nobody checked can't
get into a team, or onto support, that way. For the same reason a password is never proof of an address: support staff
(`sopops_server.auth.support`) are known by a GitHub or Google identity, never by their password.
"""

from __future__ import annotations

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import mode
from .providers import LoginProvider, register

MIN_LENGTH = 10
MAX_LENGTH = 256

_hasher = PasswordHasher()


class PasswordLogin(LoginProvider):
    id = "password"
    label = "Password"
    redirects = False
    turns_on_sign_in = False

    def configured(self) -> bool:
        return mode.required()


def check(password: str, email: str) -> None:
    """Raises ValueError, with a message that's safe to show, for a password that isn't allowed."""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"a password needs at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"a password can have at most {MAX_LENGTH} characters")
    if password.strip().lower() == email.strip().lower():
        raise ValueError("a password can't be your email address")


def hash(password: str) -> str:
    return _hasher.hash(password)


def verify(stored: str | None, password: str) -> bool:
    """Whether the password matches the stored hash. With no hash, checks a dummy one, so it takes as long."""
    try:
        return _hasher.verify(stored or _dummy(), password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    """Hashed with older parameters than today's: hash it again while the password is at hand."""
    return _hasher.check_needs_rehash(stored)


@cache
def _dummy() -> str:
    return _hasher.hash("not anyone's password")


register(PasswordLogin())
