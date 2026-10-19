"""How every route is authenticated, in one table (`ROUTES`), and the guard that enforces it on every request.

| Kind | With sign-in on | With sign-in off |
|---|---|---|
| `open` | anyone (the sign-in flow, email sign-in links, passwords, sign-up, join links, the UI's files, health, API docs) | anyone |
| `token` | the route checks its own token (ingest, runtime or onboarding); no login session | same |
| `webhook` | the route checks the sender's signature or secret; no login session | same |
| `token_or_session` | a login session, or the route's runtime token (prompt fetches) | as before |
| `signed_in` | a login session, even with no membership (sign out, accept an invite) | open |
| `session` | a login session with a membership in the workspace's org (or the session's org) | open |
| `admin` | the same, as an owner or admin of that org | open |

A route missing from the table needs a login session (fail closed), and `tests/test_auth.py` checks that every route is
listed. Workspaces in another org answer 404, as if they weren't there.

**CSRF.** Cookie-authenticated requests that change things (POST, PUT, PATCH, DELETE) must send `X-CSRF-Token` with
the value of the `sopops_csrf` cookie, which is an HMAC of the session token (`service.csrf_token`): another site can
make the browser send the session cookie, but can't read the CSRF cookie to copy it into a header. The session cookie
is also SameSite=Lax. Token and webhook routes take no cookie, so they need no CSRF token. The open routes that sign in
(a password, an email link, an invite) have no session to check against; they take only JSON, which another site can't
make a browser send without passing CORS.
"""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from starlette.responses import Response

from ..errors import NotFound
from ..tokens import Forbidden, Unauthorized
from . import actor, github, google, mode  # noqa: F401  (github and google register their providers)
from . import email, password  # noqa: F401, I001  (register their providers after them, so they're listed last)
from .service import MANAGERS, Accounts, Current, csrf_token

OPEN, TOKEN, WEBHOOK, TOKEN_OR_SESSION, SIGNED_IN, SESSION, ADMIN = "open", "token", "webhook", "token_or_session", "signed_in", "session", "admin"
KINDS = (OPEN, TOKEN, WEBHOOK, TOKEN_OR_SESSION, SIGNED_IN, SESSION, ADMIN)

SESSION_COOKIE = "sopops_session"
CSRF_COOKIE = "sopops_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
NO_MEMBERSHIP = "you're not a member of an org on this sopops yet. Ask an owner to invite you"

# Every route, by name (the operation id). Anything not listed needs a login session.
ROUTES: dict[str, str] = {
    # open: the sign-in flow, the UI's files, health and the API docs
    "spa": OPEN, "healthz": OPEN, "openapi": OPEN, "swagger_ui_html": OPEN, "swagger_ui_redirect": OPEN, "redoc_html": OPEN,
    "auth_session": OPEN, "sign_in": OPEN, "auth_callback": OPEN, "lookup_invite": OPEN, "request_email_link": OPEN, "confirm_email_link": OPEN,
    "password_login": OPEN, "first_account": OPEN, "sign_up": OPEN, "join_with_password": OPEN, "request_password_link": OPEN, "lookup_password_link": OPEN,
    "set_password": OPEN, "lookup_join_link": OPEN, "join_with_link": OPEN,
    "onboarding_skill": OPEN,  # SKILL.md, the same text the repo publishes
    # signed in, with or without a membership
    "sign_out": SIGNED_IN, "accept_invite": SIGNED_IN, "switch_org": SIGNED_IN, "change_password": SIGNED_IN, "accept_join_link": SIGNED_IN,
    # tokens: ingest (sessions), runtime (served calls), onboarding (a coding agent through sopops-mcp)
    "ingest_session": TOKEN, "open_session": TOKEN, "append_session_events": TOKEN, "close_session": TOKEN,
    "record_served": TOKEN,
    "onboarding_session": TOKEN, "onboarding_progress": TOKEN, "onboarding_push": TOKEN, "onboarding_pull_request": TOKEN,
    "onboarding_open_pull_request": TOKEN, "onboarding_check": TOKEN, "onboarding_kickoff": TOKEN, "onboarding_guide": TOKEN,
    "onboarding_extraction": TOKEN, "onboarding_project": TOKEN,
    # prompt fetches: an agent's runtime token, or someone signed in looking
    "get_agent": TOKEN_OR_SESSION, "get_prompt": TOKEN_OR_SESSION, "get_sop": TOKEN_OR_SESSION,
    # webhooks, signed by the platform
    "github_webhook": WEBHOOK, "livekit_webhook": WEBHOOK, "elevenlabs_post_call": WEBHOOK, "elevenlabs_conversation_init": WEBHOOK,
    "vapi_assistant_request": WEBHOOK,
    # the org: owners and admins manage it
    "get_org": SESSION, "list_members": SESSION,
    "create_team": SESSION,  # anyone in an org can make another team, which they own
    "set_org_platforms": SESSION,  # anyone in the org makes the first pick; after that, owners and admins (the route checks)
    "update_org": ADMIN, "add_members": ADMIN, "set_member_role": ADMIN, "remove_member": ADMIN, "list_invites": ADMIN, "create_invite": ADMIN,
    "resend_invite": ADMIN, "revoke_invite": ADMIN, "create_password_link": ADMIN, "set_up_org": ADMIN, "get_join_link": ADMIN,
    "create_join_link": ADMIN, "delete_join_link": ADMIN,
    # the GitHub App is one per sopops: made by an owner or admin
    "github_app_manifest": ADMIN, "github_app_manifest_callback": ADMIN,
    # everything else in the app: any member
    **{name: SESSION for name in (
        "list_delivery", "set_delivery", "list_platforms", "set_platform_secret", "delete_platform_secret", "test_platform_key", "list_pushes",
        "retry_pushes", "validate", "render", "migrate_files", "lint", "list_workspaces", "list_files", "edit_files", "replace_files", "get_file",
        "file_history", "get_config", "config_list_agents", "config_create_agent", "config_get_agent", "config_put_agent", "config_delete_agent",
        "config_agent_history", "config_agent_version", "config_list_instructions", "config_create_instruction", "config_get_instruction",
        "config_put_instruction", "config_delete_instruction", "config_instruction_history", "config_instruction_version", "config_list_procedures",
        "config_create_procedure", "config_get_procedure", "config_put_procedure", "config_delete_procedure", "config_procedure_history",
        "config_procedure_version", "config_list_groups", "config_create_group", "config_get_group", "config_put_group", "config_delete_group",
        "config_group_history", "config_group_version", "add_block", "remove_block", "preview_agent", "migrate", "get_settings", "put_settings",
        "settings_history", "settings_version", "draft", "create_release", "list_releases", "get_release", "activate", "publish", "list_agents",
        "list_fetches", "list_sessions", "stream_sessions", "get_session", "get_capture", "set_livekit", "delete_livekit", "test_livekit",
        "set_post_call_secret", "delete_post_call_secret", "set_elevenlabs_live", "list_tokens", "create_token", "revoke_token", "list_patterns",
        "get_pattern", "recluster_patterns", "review_queue", "set_verdict", "override_finding", "export_regression", "calibrate",
        "list_suggestions", "get_suggestion", "suggest_edit", "apply_suggestion", "rewrite_suggestion", "dismiss_suggestion", "insights_overview",
        "insights_procedure", "insights_rule", "insights_compare", "insights_report", "get_rule_lineage", "link_rule", "unlink_rule",
        "rebuild_lineage", "model_usage", "model_prices", "get_repo", "connect_repo", "inspect_repo_project", "disconnect_repo", "pull_repo",
        "sync_repo", "send_repo_drafts", "get_repo_conflict", "resolve_repo_conflict", "get_github_app", "github_install_url", "github_setup",
        "list_github_installations", "list_installation_repositories", "onboarding_capabilities", "list_platform_agents", "list_imports",
        "create_import", "import_sopc_project", "set_import_originals", "start_agent_import", "get_import", "import_events", "import_stream",
        "update_import_proposal", "resolve_import_finding", "get_import_review", "decide_import_group", "edit_import_variable", "apply_import",
        "revoke_import_token", "discard_import", "open_import_pull_request",
    )},
}


def kind_of(name: str) -> str:
    return ROUTES.get(name, SESSION)


async def guard(request: Request) -> None:
    """Every route's dependency: enforce its kind (with sign-in on), and set who the request acts as."""
    actor.current.set(None)
    request.state.login = None
    route = request.scope.get("route")
    kind = kind_of(getattr(route, "name", ""))
    if kind in (OPEN, TOKEN, WEBHOOK) or not mode.required():
        return
    accounts: Accounts = request.app.state.accounts
    token = request.cookies.get(SESSION_COOKIE)
    cur = await run_in_threadpool(accounts.current, token)
    if cur is None:
        if kind == TOKEN_OR_SESSION:
            return  # the route checks its runtime token
        raise Unauthorized("sign in first")
    if request.method not in SAFE_METHODS:
        _check_csrf(request, token or "")
    request.state.login = cur
    org = cur.org_id
    ws = request.path_params.get("workspace")
    if ws is not None:
        owner = await run_in_threadpool(accounts.workspace_org, ws)
        if owner is not None and owner not in cur.memberships:
            if kind == TOKEN_OR_SESSION:
                request.state.login = None
                return
            raise NotFound(f"no workspace '{ws}'")
        org = owner or org
    if kind != SIGNED_IN:
        if org is None:
            raise Forbidden(NO_MEMBERSHIP)
        if kind == ADMIN and cur.memberships[org] not in MANAGERS:
            raise Forbidden("only the org's owners and admins can do that")
    actor.current.set(actor.Actor(user_id=cur.user.id, name=cur.user.name, email=cur.user.email, org_id=org,
                                  role=cur.memberships.get(org) if org else None))


def signed_in(request: Request) -> Current | None:
    """The request's login session, once the guard has checked it (None with sign-in off, or none sent)."""
    return getattr(request.state, "login", None)


def _check_csrf(request: Request, session_token: str) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, csrf_token(session_token)):
        raise Forbidden(f"missing or wrong CSRF token: send the {CSRF_COOKIE} cookie's value in the {CSRF_HEADER} header")


# --- cookies ---------------------------------------------------------------------------


def secure(request: Request) -> bool:
    return request.url.scheme == "https"


def set_session_cookies(response: Response, request: Request, token: str, max_age: int) -> None:
    s = secure(request)
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, path="/", httponly=True, samesite="lax", secure=s)
    response.set_cookie(CSRF_COOKIE, csrf_token(token), max_age=max_age, path="/", httponly=False, samesite="lax", secure=s)


def clear_session_cookies(response: Response, request: Request) -> None:
    s = secure(request)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=s)
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=False, samesite="lax", secure=s)
