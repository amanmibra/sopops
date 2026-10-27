"""The demo workspace: one workspace that anyone signed in can look at, and nobody but its own org can change.

SOPOPS_DEMO_WORKSPACE names it (unset: no demo). It holds the walkthrough's Harbor Lane data, and the app's guided tour
runs over it, so someone signed in can see the whole loop on calls that aren't theirs without leaving their account.

For someone outside its org the guard (`access.guard`) lets reads through (GET, and the agent preview, which compiles a
draft without saving it) and refuses everything else with `READ_ONLY`, owner and admin routes included. Its own org, if
anyone is in it, works in it as usual. Every other workspace is unaffected: another org's still answers 404.
"""

from __future__ import annotations

import os

ENV = "SOPOPS_DEMO_WORKSPACE"
READ_ONLY = "this is the demo workspace, so nothing in it can be changed. Make changes in your own team's workspace"
# POSTs that don't change anything: the agent editor compiles the draft it's showing as someone types.
READS = frozenset({"preview_agent"})


def workspace() -> str | None:
    """The demo workspace's name, or None when there isn't one. Read on every request, so it follows the environment."""
    return os.environ.get(ENV, "").strip() or None
