"""The GitHub App: signing in as it, installing it on a repository, and creating one from sopops.

**Config.** From the environment: SOPOPS_GITHUB_APP_ID, SOPOPS_GITHUB_APP_PRIVATE_KEY (the PEM, or
SOPOPS_GITHUB_APP_PRIVATE_KEY_PATH for a file), SOPOPS_GITHUB_WEBHOOK_SECRET and SOPOPS_GITHUB_APP_SLUG (for the install
link). Without them, an App created from sopops with GitHub's manifest flow (`manifest`, then `convert`), whose key and
webhook secret are stored encrypted with SOPOPS_SECRET_KEY (`github_apps`). The environment wins.

**Auth.** The App signs a JWT (RS256, ten minutes at most) and exchanges it for an installation token
(`installation_token`), which lasts an hour; tokens are cached per installation until five minutes before they expire.

**Install.** `install_url` sends the user to `https://github.com/apps/<slug>/installations/new` with a signed `state`
naming the workspace and the page to come back to; GitHub redirects to the App's setup URL (`/v1/github/setup`) with the
installation id, which is recorded for that workspace (`record_installation`). Its repositories are then listed to pick
one (`repositories`).

**Webhooks** are signed with the webhook secret (`X-Hub-Signature-256`, HMAC-SHA256 of the body; `verify`).

Keys, tokens and secrets are never logged or returned.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

import jwt
from sqlmodel import Session, select

from .github_client import Client, GitHubError, api_url
from .orm import GitHubApp, GitHubInstallation
from .runtime import crypto

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.github")

APP_ID_ENV = "SOPOPS_GITHUB_APP_ID"
KEY_ENV = "SOPOPS_GITHUB_APP_PRIVATE_KEY"
KEY_PATH_ENV = "SOPOPS_GITHUB_APP_PRIVATE_KEY_PATH"
SECRET_ENV = "SOPOPS_GITHUB_WEBHOOK_SECRET"
SLUG_ENV = "SOPOPS_GITHUB_APP_SLUG"
WEB_ENV = "SOPOPS_GITHUB_URL"  # github.com, or a GitHub Enterprise host's web address
ROW = "default"
REFRESH_BEFORE = timedelta(minutes=5)  # an installation token is minted again this long before it expires
STATE_TTL = timedelta(hours=1)
PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}
# installation and installation_repositories are sent to every App; GitHub doesn't accept them as subscriptions.
EVENTS = ["push", "pull_request"]


@dataclass(frozen=True)
class AppConfig:
    app_id: str
    private_key: str = field(repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    slug: str | None = None
    name: str | None = None
    html_url: str | None = None
    source: str = "env"  # env | db


def web_url() -> str:
    return os.environ.get(WEB_ENV, "https://github.com").rstrip("/")


def config(store: Store) -> AppConfig | None:
    """The App to sign in as: the environment's, else the one created from sopops, else None."""
    app_id = os.environ.get(APP_ID_ENV, "").strip()
    if app_id:
        key = os.environ.get(KEY_ENV, "").strip()
        path = os.environ.get(KEY_PATH_ENV, "").strip()
        if not key and path:
            try:
                key = Path(path).expanduser().read_text().strip()
            except OSError as e:
                raise GitHubError(f"can't read {KEY_PATH_ENV}: {type(e).__name__}", 503) from None
        if not key:
            raise GitHubError(f"{APP_ID_ENV} is set, but neither {KEY_ENV} nor {KEY_PATH_ENV} is", 503)
        return AppConfig(app_id, key.replace("\\n", "\n"), os.environ.get(SECRET_ENV, "").strip() or None,
                         os.environ.get(SLUG_ENV, "").strip() or None, source="env")
    with Session(store.engine) as db:
        row = db.get(GitHubApp, ROW)
    if row is None:
        return None
    return AppConfig(row.app_id, crypto.decrypt(row.key_ciphertext), crypto.decrypt(row.secret_ciphertext), row.slug, row.name,
                     row.html_url, source="db")


def webhook_secret(store: Store) -> str | None:
    """The secret webhooks are signed with: the environment's (also usable without an App, for a repository webhook),
    else the stored App's."""
    env = os.environ.get(SECRET_ENV, "").strip()
    if env:
        return env
    cfg = config(store)
    return cfg.webhook_secret if cfg else None


def status(store: Store) -> dict:
    """What the UI shows of the App: never the key or the secret."""
    try:
        cfg = config(store)
    except (GitHubError, crypto.SecretKeyMissing) as e:
        return {"configured": False, "source": None, "app_id": None, "slug": None, "name": None, "html_url": None,
                "install_url": None, "can_create": False, "problem": str(e)}
    can_create = cfg is None and bool(os.environ.get(crypto.ENV, "").strip())
    if cfg is None:
        return {"configured": False, "source": None, "app_id": None, "slug": None, "name": None, "html_url": None,
                "install_url": None, "can_create": can_create,
                "problem": None if can_create else f"set {crypto.ENV} to create a GitHub App from sopops"}
    return {"configured": True, "source": cfg.source, "app_id": cfg.app_id, "slug": cfg.slug, "name": cfg.name or cfg.slug,
            "html_url": cfg.html_url or (f"{web_url()}/apps/{cfg.slug}" if cfg.slug else None),
            "install_url": f"{web_url()}/apps/{cfg.slug}/installations/new" if cfg.slug else None, "can_create": False,
            "problem": None if cfg.slug else f"set {SLUG_ENV} so sopops can link to the App's install page"}


def require(store: Store) -> AppConfig:
    cfg = config(store)
    if cfg is None:
        raise GitHubError("no GitHub App is set up: set SOPOPS_GITHUB_APP_ID and its private key, or create one in Settings", 400)
    return cfg


# --- signing in -------------------------------------------------------------------------------------


def app_jwt(cfg: AppConfig, now: float | None = None) -> str:
    """The App's JWT: issued a minute in the past (clock drift), valid nine minutes (GitHub allows ten)."""
    now = int(now if now is not None else time.time())
    try:
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(cfg.app_id)}, cfg.private_key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError):
        raise GitHubError("the GitHub App's private key isn't a valid PEM RSA key", 503) from None


_tokens: dict[tuple[str, str, int], tuple[str, datetime]] = {}
_lock = threading.Lock()


def installation_token(store: Store, installation_id: int) -> str:
    """A token for the installation, from the cache while it has more than REFRESH_BEFORE left."""
    cfg = require(store)
    key = (api_url(), cfg.app_id, int(installation_id))
    now = datetime.now(timezone.utc)
    with _lock:
        hit = _tokens.get(key)
        if hit and hit[1] - now > REFRESH_BEFORE:
            return hit[0]
    with Client(app_jwt(cfg)) as gh:
        got = gh.post(f"/app/installations/{int(installation_id)}/access_tokens", {}, what=f"the GitHub App's installation {installation_id}")
    token = got["token"]
    try:
        expires = datetime.fromisoformat(str(got.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        expires = now + timedelta(minutes=55)
    with _lock:
        _tokens[key] = (token, expires)
    return token


def forget_tokens(installation_id: int | None = None) -> None:
    """Drop cached installation tokens (one installation's, or all): after it's deleted, or in tests."""
    with _lock:
        for k in [k for k in _tokens if installation_id is None or k[2] == int(installation_id)]:
            del _tokens[k]


def installation(store: Store, installation_id: int) -> dict:
    """The installation, read as the App (so it must be this App's): {id, account, suspended}."""
    cfg = require(store)
    with Client(app_jwt(cfg)) as gh:
        got = gh.get(f"/app/installations/{int(installation_id)}", what=f"installation {installation_id} of the GitHub App")
    return {"id": got["id"], "account": (got.get("account") or {}).get("login"), "suspended": bool(got.get("suspended_at"))}


def repositories(store: Store, installation_id: int) -> list[dict]:
    """The repositories the installation can reach (up to 1,000), as {owner, repo, full_name, default_branch, private}."""
    with Client(installation_token(store, installation_id)) as gh:
        out: list[dict] = []
        for page in range(1, 11):
            got = gh.get("/installation/repositories", params={"per_page": 100, "page": page}, what="the installation's repositories")
            repos = got.get("repositories") or []
            out += [{"owner": r["owner"]["login"], "repo": r["name"], "full_name": r["full_name"], "default_branch": r.get("default_branch") or "main",
                     "private": bool(r.get("private"))} for r in repos]
            if len(repos) < 100:
                break
    return sorted(out, key=lambda r: r["full_name"].lower())


def installations(store: Store, workspace: str) -> list[dict]:
    with Session(store.engine) as db:
        rows = db.exec(select(GitHubInstallation).where(GitHubInstallation.workspace == workspace).order_by(GitHubInstallation.created_at))
        return [{"installation_id": r.installation_id, "account": r.account} for r in rows]


def has_installation(store: Store, workspace: str, installation_id: int) -> bool:
    with Session(store.engine) as db:
        return db.get(GitHubInstallation, (workspace, int(installation_id))) is not None


def record_installation(store: Store, workspace: str, installation_id: int) -> dict:
    """The install started from `workspace` came back: check the installation is this App's, and let the workspace use it."""
    found = installation(store, installation_id)
    with Session(store.engine) as db, db.begin():
        store.sessions._ensure_workspace(db, workspace)
        row = db.get(GitHubInstallation, (workspace, int(installation_id)))
        if row is None:
            db.add(GitHubInstallation(workspace=workspace, installation_id=int(installation_id), account=found["account"], created_at=_now()))
        else:
            row.account = found["account"]
    log.info("workspace %s may use github installation %s (%s)", workspace, installation_id, found["account"])
    return {"installation_id": int(installation_id), "account": found["account"]}


# --- signed state --------------------------------------------------------------------------------------


def _state_key(store: Store) -> bytes:
    """What `state` is signed with: SOPOPS_SECRET_KEY, else (an App from the environment) its private key."""
    secret = os.environ.get(crypto.ENV, "").strip()
    if not secret:
        cfg = config(store)
        if cfg is None:
            raise GitHubError(f"set {crypto.ENV} first", 503)
        secret = cfg.private_key
    return hashlib.sha256(b"sopops github state\0" + secret.encode()).digest()


def sign_state(store: Store, data: dict, now: float | None = None) -> str:
    body = {**data, "exp": int((now if now is not None else time.time()) + STATE_TTL.total_seconds())}
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    mac = hmac.new(_state_key(store), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{mac}"


def read_state(store: Store, state: str | None, now: float | None = None) -> dict:
    """The data a `state` carries. GitHubError (400) if it's missing, tampered with or expired."""
    raw, _, mac = (state or "").partition(".")
    if not raw or not mac or not hmac.compare_digest(mac, hmac.new(_state_key(store), raw.encode(), hashlib.sha256).hexdigest()):
        raise GitHubError("this link didn't come from sopops (its state is missing or wrong); start again from Settings", 400)
    data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    if data.get("exp", 0) < (now if now is not None else time.time()):
        raise GitHubError("this link expired; start again from Settings", 400)
    return data


def peek_return(state: str | None) -> str:
    """Where a state that didn't verify says to go back to, to show the error there: only ever a path on this server."""
    raw = (state or "").partition(".")[0]
    try:
        return safe_return(json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))).get("to"))
    except (ValueError, AttributeError):
        return "/"


def safe_return(path: str | None) -> str:
    """A page of this app to come back to: a path on the same origin, never another site."""
    path = (path or "").strip()
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def install_url(store: Store, workspace: str, return_to: str) -> str:
    cfg = require(store)
    if not cfg.slug:
        raise GitHubError(f"set {SLUG_ENV} (the App's name in its github.com/apps/ link) to install it", 503)
    state = sign_state(store, {"ws": workspace, "to": safe_return(return_to)})
    return f"{web_url()}/apps/{quote(cfg.slug)}/installations/new?{urlencode({'state': state})}"


# --- webhooks ------------------------------------------------------------------------------------------


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    """Whether `X-Hub-Signature-256` is the HMAC-SHA256 of the body with the webhook secret."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- creating an App (the manifest flow) ---------------------------------------------------------------


def manifest(store: Store, public_url: str, return_to: str, organization: str | None = None, webhook_url: str | None = None) -> dict:
    """What the browser posts to GitHub to create the App: the form's `action` (with a signed state) and the
    `manifest` field. GitHub then redirects to `/v1/github/app/manifest/callback` with a code for `convert`."""
    if config(store) is not None:
        raise GitHubError("a GitHub App is already set up", 409)
    if not os.environ.get(crypto.ENV, "").strip():
        raise GitHubError(f"set {crypto.ENV} first: the App's private key is stored encrypted with it", 503)
    base = public_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise GitHubError(f"'{public_url}' isn't this server's address (http:// or https://)", 400)
    hook = (webhook_url or "").strip() or f"{base}/v1/github/webhook"
    body = {
        "name": f"sopops {hashlib.sha256(base.encode()).hexdigest()[:6]}",
        "url": base,
        "hook_attributes": {"url": hook, "active": True},
        "redirect_url": f"{base}/v1/github/app/manifest/callback",
        "setup_url": f"{base}/v1/github/setup",
        "setup_on_update": True,
        "public": False,
        "default_permissions": PERMISSIONS,
        "default_events": EVENTS,
    }
    state = sign_state(store, {"manifest": True, "to": safe_return(return_to)})
    where = f"/organizations/{quote(organization.strip())}/settings/apps/new" if organization and organization.strip() else "/settings/apps/new"
    return {"action": f"{web_url()}{where}?{urlencode({'state': state})}", "manifest": json.dumps(body)}


def convert(store: Store, code: str) -> dict:
    """Exchange the manifest flow's code for the new App (its id, key and webhook secret), and store it."""
    if config(store) is not None:
        raise GitHubError("a GitHub App is already set up", 409)
    with Client("") as gh:
        got = gh.post(f"/app-manifests/{quote(code, safe='')}/conversions", {}, what="the new GitHub App")
    pem, secret = got.get("pem"), got.get("webhook_secret")
    if not pem or not secret:
        raise GitHubError("GitHub didn't return the new App's key and webhook secret")
    row = GitHubApp(id=ROW, app_id=str(got["id"]), slug=got.get("slug") or "", name=got.get("name") or got.get("slug") or "",
                    owner=(got.get("owner") or {}).get("login"), html_url=got.get("html_url"), key_ciphertext=crypto.encrypt(pem),
                    secret_ciphertext=crypto.encrypt(secret), created_at=_now())
    with Session(store.engine) as db, db.begin():
        db.merge(row)
    log.info("created github app %s (%s)", row.slug, row.app_id)
    return {"app_id": row.app_id, "slug": row.slug, "name": row.name, "html_url": row.html_url}


def _now() -> datetime:
    return datetime.now(timezone.utc)
