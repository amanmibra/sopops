"""GitHub's REST API: one client per token (a personal access token, an installation token or an App's JWT), and its
failures as `GitHubError`s in words safe to show, never containing the token.

The API base is SOPOPS_GITHUB_API_URL (default https://api.github.com; GitHub Enterprise: https://HOST/api/v3).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

API_ENV = "SOPOPS_GITHUB_API_URL"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    """A GitHub call or connection problem, in words safe to show (never containing the token). `status` is the
    HTTP status sopops answers with: 400 for something the user fixes (the token, the repo, the path), 409 for a
    conflict, 429 for GitHub's rate limit, 502 when GitHub fails or can't be reached."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def api_url() -> str:
    return os.environ.get(API_ENV, "https://api.github.com").rstrip("/")


class Client:
    """GitHub's REST API with one token (none for the few calls that need none), turning every failure into a
    GitHubError without the token in it."""

    def __init__(self, token: str):
        self._token = token
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": "sopops"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=api_url(), timeout=TIMEOUT, headers=headers)

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def get(self, path: str, params: dict | None = None, what: str = "it", missing: bool = False) -> Any:
        return self.request("GET", path, params=params, what=what, missing=missing)

    def post(self, path: str, body: dict, what: str = "it") -> Any:
        return self.request("POST", path, body=body, what=what)

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None, what: str = "it",
                missing: bool = False) -> Any:
        """The response's JSON. `missing`: a 404 returns None instead of raising."""
        try:
            res = self._http.request(method, path, json=body, params=params)
        except httpx.HTTPError as e:
            raise GitHubError(f"couldn't reach GitHub: {type(e).__name__}") from None
        if res.status_code == 404 and missing:
            return None
        if res.status_code >= 400:
            raise error_of(res, what, self._token)
        try:
            return res.json() if res.content else {}
        except ValueError:
            raise GitHubError("GitHub returned a response that isn't JSON") from None


def error_of(res: httpx.Response, what: str, token: str) -> GitHubError:
    """A failed response in plain words."""
    code = res.status_code
    try:
        payload = res.json()
    except ValueError:
        payload = {}
    message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        message += ": " + "; ".join(e.get("message") or e.get("code") or json.dumps(e) if isinstance(e, dict) else str(e) for e in errors)
    message = message.replace(token, "…")[:300] if token else message[:300]
    if code in (403, 429) and (res.headers.get("x-ratelimit-remaining") == "0" or res.headers.get("retry-after") or "rate limit" in message.lower()):
        if retry := res.headers.get("retry-after"):
            return GitHubError(f"GitHub's rate limit: try again in {retry} seconds", 429)
        reset = res.headers.get("x-ratelimit-reset")
        when = f" at {datetime.fromtimestamp(int(reset), timezone.utc):%H:%M} UTC" if reset and reset.isdigit() else " within the hour"
        return GitHubError(f"GitHub's rate limit for this token is used up; it resets{when}", 429)
    if code == 401:
        return GitHubError("GitHub refused the token (401): it's wrong, expired or revoked", 400)
    if code == 403:
        return GitHubError(f"the token isn't allowed to access {what} (403{': ' + message if message else ''}). A fine-grained token needs "
                           "this repository, with Contents and Pull requests: read and write, and Metadata: read", 400)
    if code == 404:
        return GitHubError(f"GitHub found no {what} (404): check the name, and that the token has access to the repository", 400)
    if code == 409:
        return GitHubError(f"GitHub refused {what} (409{': ' + message if message else ''}); an empty repository has nothing to read", 409)
    if code == 422:
        return GitHubError(f"GitHub rejected {what} (422{': ' + message if message else ''})", 400)
    return GitHubError(f"GitHub answered {code} for {what}{': ' + message if message else ''}")
