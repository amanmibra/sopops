"""GitHub: a workspace's repository connection, and pull requests for suggested edits.

A placeholder until repo connections land; both functions raise NotImplementedError, and callers treat that as "no
connection" (suggestions are then applied as drafts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

RepoConnection = Any


def repo_connection(store: Store, workspace_id: str) -> RepoConnection | None:
    """The workspace's repository connection, or None."""
    raise NotImplementedError("GitHub repo connections aren't available yet")


def open_pull_request(store: Store, workspace_id: str, files: dict[str, str], title: str, body: str) -> str:
    """Open a pull request on the connected repository with `files` ({path in the sopc project: content}). Returns its URL."""
    raise NotImplementedError("GitHub repo connections aren't available yet")
