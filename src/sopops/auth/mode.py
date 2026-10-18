"""The auth mode: whether this sopops needs people to sign in.

With no login provider configured, sopops runs with no sign-in (local use, demos). With at least one (GitHub, Google,
or email, which is on with SMTP set up or SOPOPS_EMAIL_DEV), sign-in is on.
SOPOPS_AUTH_REQUIRED (true or false) forces it either way. Read on every request, so it follows the environment.
"""

from __future__ import annotations

import os

from . import providers

ENV = "SOPOPS_AUTH_REQUIRED"


def required() -> bool:
    forced = os.environ.get(ENV, "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    return bool(providers.configured())
