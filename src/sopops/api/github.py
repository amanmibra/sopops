"""The GitHub App: its status, installing it on a repository, creating one from sopops (GitHub's App manifest flow), and
its webhooks. See `sopops.github_app` and `sopops.github_webhooks`."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete

from .. import github_app, github_webhooks
from ..errors import workspace_id
from ..github import GitHubError
from ..orm import GitHubDelivery
from ..runtime import crypto
from ..schemas.repo import GitHubAppOut, InstallationOut, InstallationRepoOut, InstallOut, ManifestIn, ManifestOut
from ..store import Store
from .deps import OPEN

log = logging.getLogger("sopops.github")

DELIVERIES_KEPT = timedelta(days=7)


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/github/app", response_model=GitHubAppOut, dependencies=OPEN, tags=["github"])
    def get_github_app() -> dict:
        """Whether a GitHub App is set up (from the environment, or created from sopops), and where to install it.
        Never its key or webhook secret."""
        return github_app.status(store)

    @r.get("/v1/workspaces/{workspace}/github/install", response_model=InstallOut, dependencies=OPEN, tags=["github"],
           responses={400: {"description": "No GitHub App is set up."}, 503: {"description": "The App has no slug, or no key to sign with."}})
    def github_install_url(workspace: str, return_to: str = "/") -> dict:
        """Where to send the browser to install the App: GitHub comes back to `/v1/github/setup`, which lets this
        workspace use the installation, then to `return_to`."""
        return {"url": github_app.install_url(store, workspace_id(workspace), return_to)}

    @r.get("/v1/github/setup", include_in_schema=False)
    def github_setup(installation_id: int | None = None, state: str | None = None, setup_action: str | None = None) -> Response:
        """The App's setup URL: GitHub sends the browser here after an install, with the installation's id and our state."""
        try:
            data = github_app.read_state(store, state)
            if not data.get("ws"):
                raise GitHubError("this link didn't come from an install started in sopops", 400)
            if installation_id is None:
                raise GitHubError("GitHub didn't say which installation this is", 400)
            github_app.record_installation(store, data["ws"], installation_id)
        except (GitHubError, crypto.SecretKeyMissing) as e:
            return _back(github_app.peek_return(state), github_error=str(e))
        return _back(data.get("to"), github_installation=str(installation_id))

    @r.get("/v1/workspaces/{workspace}/github/installations", response_model=list[InstallationOut], dependencies=OPEN, tags=["github"])
    def list_github_installations(workspace: str) -> list[dict]:
        """The App's installations this workspace may connect repositories through (installed from it)."""
        return github_app.installations(store, workspace_id(workspace))

    @r.get("/v1/workspaces/{workspace}/github/installations/{installation_id}/repositories", response_model=list[InstallationRepoOut],
           dependencies=OPEN, tags=["github"], responses={400: {"description": "Not an installation of this workspace, or GitHub refused."}})
    def list_installation_repositories(workspace: str, installation_id: int) -> list[dict]:
        """The repositories the installation can reach, to pick one."""
        if not github_app.has_installation(store, workspace_id(workspace), installation_id):
            raise HTTPException(400, "install the GitHub App from this workspace first")
        return github_app.repositories(store, installation_id)

    @r.post("/v1/github/app/manifest", response_model=ManifestOut, dependencies=OPEN, tags=["github"],
            responses={409: {"description": "An App is already set up."}, 503: {"description": "SOPOPS_SECRET_KEY isn't set."}})
    def github_app_manifest(req: ManifestIn) -> dict:
        """Create a GitHub App for this server (self-hosted, with no App yet): the form the browser posts to GitHub.
        It asks for Contents and Pull requests (read and write) and Metadata (read), and push and pull_request events."""
        return github_app.manifest(store, req.public_url, req.return_to, req.organization, req.webhook_url)

    @r.get("/v1/github/app/manifest/callback", include_in_schema=False)
    def github_app_manifest_callback(code: str | None = None, state: str | None = None) -> Response:
        """GitHub created the App: exchange the code for its id, key and webhook secret, and store them encrypted."""
        try:
            data = github_app.read_state(store, state)
            if not data.get("manifest"):
                raise GitHubError("this link didn't come from creating an App in sopops", 400)
            if not code:
                raise GitHubError("GitHub didn't send the new App's code", 400)
            github_app.convert(store, code)
        except (GitHubError, crypto.SecretKeyMissing) as e:
            return _back(github_app.peek_return(state), github_error=str(e))
        return _back(data.get("to"), github_app="created")

    @r.post("/v1/github/webhook", tags=["github"], responses={
        401: {"description": "Unsigned, or the signature doesn't match the webhook secret."}, 503: {"description": "No webhook secret set up."}})
    async def github_webhook(request: Request, background: BackgroundTasks, x_github_event: str | None = Header(None),
                             x_github_delivery: str | None = Header(None), x_hub_signature_256: str | None = Header(None)) -> dict:
        """GitHub's webhooks, signed with the webhook secret (X-Hub-Signature-256). Each delivery is handled once
        (X-GitHub-Delivery); the work runs after the response, so GitHub doesn't wait."""
        body = await request.body()
        secret = github_app.webhook_secret(store)
        if not secret:
            raise HTTPException(503, "no webhook secret is set up (SOPOPS_GITHUB_WEBHOOK_SECRET, or an App created from sopops)")
        if not github_app.verify(secret, body, x_hub_signature_256):
            log.warning("rejected a github webhook (%s) with a missing or wrong signature", x_github_event)
            raise HTTPException(401, "the webhook's signature doesn't match the webhook secret")
        event = (x_github_event or "").strip()
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(400, "the webhook's body isn't JSON") from None
        if event == "ping":
            return {"ok": True, "event": "ping"}
        if x_github_delivery:
            if not _claim(store, x_github_delivery[:64], event):
                return {"ok": True, "event": event, "duplicate": True}
        background.add_task(_handle, store, event, payload, x_github_delivery)
        return {"ok": True, "event": event, "duplicate": False}

    return r


def _claim(store: Store, delivery: str, event: str) -> bool:
    """Record the delivery; False if it was handled before. Deliveries older than a week are forgotten."""
    now = datetime.now(timezone.utc)
    try:
        with Session(store.engine) as db, db.begin():
            db.exec(delete(GitHubDelivery).where(GitHubDelivery.received_at < now - DELIVERIES_KEPT))
            db.add(GitHubDelivery(id=delivery, event=event[:64], received_at=now))
    except IntegrityError:
        return False
    return True


def _handle(store: Store, event: str, payload: dict, delivery: str | None) -> None:
    try:
        out = github_webhooks.handle(store, event, payload)
        log.info("github webhook %s (%s): %s", event, delivery, out)
    except Exception:  # GitHub won't redeliver on its own: forget the delivery, so a manual redelivery runs it again
        log.exception("handling github webhook %s (%s) failed", event, delivery)
        if delivery:
            with Session(store.engine) as db, db.begin():
                db.exec(delete(GitHubDelivery).where(GitHubDelivery.id == delivery[:64]))


def _back(to: str | None, **params: str) -> RedirectResponse:
    """Back to the page the flow started on, with what happened in the query string."""
    path = github_app.safe_return(to)
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{urlencode(params)}", status_code=303)
