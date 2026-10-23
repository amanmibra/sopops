"""Sign-in: GitHub and Google behind respx (no network), email links (dev mode's log, and a fake SMTP server), passwords
(the first account, invites, reset links, rate limits), who joins which org, adding people, login sessions, CSRF, roles,
authors, the route auth table in both modes, and token and webhook routes working without a session."""

import base64
import json
import logging
import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
import respx
from conftest import SOPC_BIN, WS, downgrade, fresh_db_url
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from sopops_server import Sopc, Store, cli, create_app, mail
from sopops_server.auth import access, mode, providers
from sopops_server.auth.service import csrf_token
from sopops_server.db import upgrade
from sopops_server.orm import EmailLink, FileVersion, Identity, Invite, LoginAttempt, LoginSession, Membership, Org, PasswordLink, User, Workspace

GH_TOKEN = "https://github.com/login/oauth/access_token"
GH_API = "https://api.github.com"
G_TOKEN = "https://oauth2.googleapis.com/token"
G_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


@pytest.fixture
def providers_env(monkeypatch):
    for k, v in {"SOPOPS_GITHUB_OAUTH_CLIENT_ID": "gh-client", "SOPOPS_GITHUB_OAUTH_CLIENT_SECRET": "gh-secret",
                 "SOPOPS_GOOGLE_CLIENT_ID": "g-client", "SOPOPS_GOOGLE_CLIENT_SECRET": "g-secret"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def app(tmp_path, providers_env):
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


@pytest.fixture
def owner(app, files):
    """The first person to sign in (the owner), with a workspace that existed before."""
    c = TestClient(app)
    c.app.state.store.replace("demo", files)  # sign-in off at this point in the store: like a workspace made before
    sign_in(c, "github", "1", "ana@example.com", name="Ana")
    return c


class GitHub:
    """GitHub's token endpoint, /user and /user/emails for one account."""

    def __init__(self, router: respx.MockRouter, id: str, email: str, verified: bool = True, name: str = "", primary: bool = True):
        router.post(GH_TOKEN).mock(side_effect=self.token)
        router.get(f"{GH_API}/user").mock(return_value=httpx.Response(200, json={"id": int(id), "login": f"user{id}", "name": name or None,
                                                                                  "avatar_url": f"https://avatars.example/{id}"}))
        emails = [{"email": email, "primary": primary, "verified": verified}, {"email": f"other{id}@example.net", "primary": not primary, "verified": True}]
        router.get(f"{GH_API}/user/emails").mock(return_value=httpx.Response(200, json=emails))
        self.sent: list[dict] = []

    def token(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "gho_test", "token_type": "bearer", "scope": "read:user,user:email"})


class Google:
    def __init__(self, router: respx.MockRouter, sub: str, email: str, verified: bool = True, name: str = ""):
        router.post(G_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "ya29.test", "token_type": "Bearer", "expires_in": 3600}))
        router.get(G_USERINFO).mock(return_value=httpx.Response(200, json={"sub": sub, "email": email, "email_verified": verified, "name": name,
                                                                          "picture": "https://pictures.example/p"}))


def offered(id: str, label: str, redirects: bool = True) -> dict:
    """A provider as /v1/auth/session lists it. `owns_teams` is false for all of these: GitHub, Google, email and
    password leave teams to sopops, so there's no provider page to send anyone to."""
    return {"id": id, "label": label, "redirects": redirects, "owns_teams": False, "new_team_url": None}


def start(c: TestClient, provider: str, next: str = "/") -> str:
    """Begin signing in; the state the provider will send back."""
    res = c.get(f"/v1/auth/login/{provider}", params={"next": next}, follow_redirects=False)
    assert res.status_code == 302, res.text
    q = parse_qs(urlparse(res.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert q["redirect_uri"] == [f"{str(c.base_url).rstrip('/')}/v1/auth/callback/{provider}"]
    return q["state"][0]


def sign_in(c: TestClient, provider: str, id: str, email: str, verified: bool = True, name: str = "", next: str = "/") -> httpx.Response:
    """The whole round trip with the provider mocked. Afterwards the client sends its CSRF token, as the web UI does."""
    with respx.mock(assert_all_called=False) as router:
        GitHub(router, id, email, verified, name) if provider == "github" else Google(router, id, email, verified, name)
        st = start(c, provider, next)
        res = c.get(f"/v1/auth/callback/{provider}", params={"code": "the-code", "state": st}, follow_redirects=False)
    if c.cookies.get(access.CSRF_COOKIE):
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    return res


def error_of(res: httpx.Response) -> str:
    assert res.status_code == 303 and res.headers["location"].startswith("/sign-in?error="), res.headers.get("location")
    return unquote(res.headers["location"].split("error=", 1)[1])


def db(c: TestClient) -> Session:
    return Session(c.app.state.store.engine)


# --- providers --------------------------------------------------------------------------


def test_the_mode_follows_the_providers_and_the_override(monkeypatch, providers_env):
    assert mode.required()
    monkeypatch.setenv("SOPOPS_AUTH_REQUIRED", "false")
    assert not mode.required()
    for k in ("SOPOPS_GITHUB_OAUTH_CLIENT_ID", "SOPOPS_GOOGLE_CLIENT_ID"):
        monkeypatch.delenv(k)
    monkeypatch.delenv("SOPOPS_AUTH_REQUIRED")
    assert not mode.required()
    monkeypatch.setenv("SOPOPS_AUTH_REQUIRED", "true")
    assert mode.required()


def test_github_sign_in_makes_a_user_and_a_session(app, files):
    c = TestClient(app)
    assert c.get("/v1/auth/session").json() == {"required": True, "providers": [offered("github", "GitHub"), offered("google", "Google"),
                                                                              offered("password", "Password", redirects=False)],
                                                 "user": None, "org": None, "orgs": [], "first_account": True, "password_email": False, "sign_up": False, "open_signup": False,
                                                 "platforms": None}
    with respx.mock(assert_all_called=False) as router:
        gh = GitHub(router, "42", "Ana@Example.com", name="Ana")
        st = start(c, "github", next="/w/demo/sessions")
        res = c.get("/v1/auth/callback/github", params={"code": "the-code", "state": st}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/w/demo/sessions"
    assert gh.sent[0]["code_verifier"][0] and gh.sent[0]["code"] == ["the-code"]  # PKCE
    cookies = res.headers.get_list("set-cookie")
    session = next(h for h in cookies if h.startswith("sopops_session="))
    assert "HttpOnly" in session and "SameSite=lax" in session and "Secure" not in session  # http here; Secure on https
    me = c.get("/v1/auth/session").json()
    assert me["user"]["email"] == "ana@example.com" and me["user"]["name"] == "Ana" and me["org"]["role"] == "owner"
    with db(c) as d:
        token = c.cookies.get(access.SESSION_COOKIE)
        row = d.exec(select(LoginSession)).one()
        assert row.token_hash != token and token not in json.dumps(row.model_dump(), default=str)  # stored hashed


def test_the_state_must_match_and_come_from_here(app):
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        GitHub(router, "42", "ana@example.com")
        start(c, "github")
        assert "expired" in error_of(c.get("/v1/auth/callback/github", params={"code": "c", "state": "forged"}, follow_redirects=False))
        fresh = TestClient(app)  # no flow cookie: the sign-in didn't start in this browser
        assert "expired" in error_of(fresh.get("/v1/auth/callback/github", params={"code": "c", "state": "x"}, follow_redirects=False))
    assert error_of(c.get("/v1/auth/callback/github", params={"error": "access_denied"}, follow_redirects=False)) == "you cancelled signing in"
    assert c.get("/v1/auth/login/okta", follow_redirects=False).status_code == 404
    res = c.get("/v1/auth/login/github", params={"next": "https://evil.example/"}, follow_redirects=False)
    flow = json.loads(base64.urlsafe_b64decode(res.cookies.get("sopops_oauth")))
    assert flow["n"] == "/"  # never back to another site


def test_unverified_emails_are_refused(app):
    c = TestClient(app)
    assert "no verified email" in error_of(sign_in(c, "google", "g-1", "eve@example.com", verified=False))
    with respx.mock(assert_all_called=False) as router:
        GitHub(router, "7", "eve@example.com", verified=False)
        st = start(c, "github")
        assert "no verified email" in error_of(c.get("/v1/auth/callback/github", params={"code": "c", "state": st}, follow_redirects=False))
    with db(c) as d:
        assert d.exec(select(User)).all() == [] and d.exec(select(Identity)).all() == []
    assert c.get("/v1/workspaces").status_code == 401


def test_the_same_verified_email_links_both_providers(owner, app):
    with db(owner) as d:
        ana = d.exec(select(User)).one()
    other = TestClient(app)
    sign_in(other, "google", "g-ana", "ANA@example.com", name="Ana G")
    assert other.get("/v1/auth/session").json()["user"]["id"] == ana.id
    with db(owner) as d:
        assert sorted(i.provider for i in d.exec(select(Identity).where(Identity.user_id == ana.id))) == ["github", "google"]
        assert len(d.exec(select(User)).all()) == 1
    again = TestClient(app)  # the identity finds the user even after the email changes at GitHub
    sign_in(again, "github", "1", "ana@new.example.com")
    assert again.get("/v1/auth/session").json()["user"]["id"] == ana.id


# --- who joins --------------------------------------------------------------------------


def test_the_first_user_owns_the_existing_org_and_the_next_needs_an_invite(owner, app):
    me = owner.get("/v1/auth/session").json()
    assert me["org"]["role"] == "owner" and me["org"]["name"] == "Default"
    assert [w["workspace"] for w in owner.get("/v1/workspaces").json()] == ["demo"]

    bob = TestClient(app)
    sign_in(bob, "github", "2", "bob@example.com")
    state = bob.get("/v1/auth/session").json()
    assert state["user"]["email"] == "bob@example.com" and state["org"] is None and state["orgs"] == []  # the "ask an owner" page
    res = bob.get("/v1/workspaces")
    assert res.status_code == 403 and "Ask an owner to invite you" in res.json()["detail"]
    assert bob.get(f"{WS}/files").status_code == 404  # another org's workspace isn't there for him


def test_an_invite_link_joins_the_org(owner, app):
    created = owner.post("/v1/org/invites", json={"email": "Bob@Example.com", "role": "admin"})
    assert created.status_code == 201
    link = created.json()["link"]
    assert link.startswith("http://testserver/invite#") and created.json()["email"] == "bob@example.com"
    token = link.split("#", 1)[1]
    assert [i["email"] for i in owner.get("/v1/org/invites").json()] == ["bob@example.com"]
    assert token not in json.dumps(owner.get("/v1/org/invites").json())

    anon = TestClient(app)
    info = anon.post("/v1/auth/invites/lookup", json={"token": token}).json()
    assert info["org_name"] == "Default" and info["email"] == "bob@example.com" and info["role"] == "admin" and not info["accepted"]

    mallory = TestClient(app)
    sign_in(mallory, "github", "3", "mallory@example.com")
    res = mallory.post("/v1/auth/invites/accept", json={"token": token})
    assert res.status_code == 403 and "for bob@example.com" in res.json()["detail"]

    bob = TestClient(app)
    sign_in(bob, "google", "g-bob", "bob@example.com")  # signing in with the invited email accepts it
    assert bob.get("/v1/auth/session").json()["org"]["role"] == "admin"
    assert anon.post("/v1/auth/invites/lookup", json={"token": token}).status_code == 404  # used
    assert bob.post("/v1/auth/invites/lookup", json={"token": token}).json()["accepted"]  # by him: the page says he's in
    assert mallory.post("/v1/auth/invites/lookup", json={"token": token}).status_code == 404
    assert owner.get("/v1/org/invites").json() == []


def test_accepting_an_invite_once_signed_in(owner, app):
    carol = TestClient(app)
    sign_in(carol, "github", "4", "carol@example.com")
    assert carol.get("/v1/auth/session").json()["org"] is None
    token = owner.post("/v1/org/invites", json={"email": "carol@example.com"}).json()["link"].split("#")[1]
    joined = carol.post("/v1/auth/invites/accept", json={"token": token})
    assert joined.status_code == 200 and joined.json()["org"]["role"] == "member"
    assert [w["workspace"] for w in carol.get("/v1/workspaces").json()] == ["demo"]
    revoked = owner.post("/v1/org/invites", json={"email": "dan@example.com"}).json()
    assert owner.delete(f"/v1/org/invites/{revoked['id']}").status_code == 204
    assert TestClient(app).post("/v1/auth/invites/lookup", json={"token": revoked["link"].split("#")[1]}).status_code == 404


def test_allowed_email_domains(owner, app, monkeypatch):
    res = owner.patch("/v1/org", json={"allowed_domains": ["Example.org", "@example.org", "team.example"]})
    assert res.status_code == 200 and res.json()["allowed_domains"] == ["example.org", "team.example"]
    assert owner.patch("/v1/org", json={"allowed_domains": ["not a domain"]}).status_code == 400
    dee = TestClient(app)
    sign_in(dee, "google", "g-dee", "dee@example.org")
    assert dee.get("/v1/auth/session").json()["org"]["role"] == "member"
    out = TestClient(app)
    sign_in(out, "google", "g-out", "out@elsewhere.example")
    assert out.get("/v1/auth/session").json()["org"] is None

    monkeypatch.setenv("SOPOPS_ALLOWED_EMAIL_DOMAINS", "example.org")  # server-wide: nobody else signs in at all
    assert "doesn't take sign-ins from example.com" in error_of(sign_in(TestClient(app), "github", "1", "ana@example.com"))
    assert sign_in(TestClient(app), "google", "g-dee", "dee@example.org").status_code == 303


# --- sessions and CSRF ------------------------------------------------------------------


def test_sign_out_revokes_the_session(owner, app):
    cookie = owner.cookies.get(access.SESSION_COOKIE)
    assert owner.get("/v1/workspaces").status_code == 200
    out = owner.post("/v1/auth/logout")
    assert out.status_code == 204
    assert any(h.startswith("sopops_session=") and "Max-Age=0" in h for h in out.headers.get_list("set-cookie"))
    stolen = TestClient(app, cookies={access.SESSION_COOKIE: cookie})  # the old cookie, kept somewhere, no longer works
    assert stolen.get("/v1/workspaces").status_code == 401
    with db(owner) as d:
        assert d.exec(select(LoginSession)).one().revoked_at is not None


def test_sessions_expire_and_slide(owner):
    with db(owner) as d, d.begin():
        s = d.exec(select(LoginSession)).one()
        s.last_seen_at = datetime.now(timezone.utc) - timedelta(days=2)
        s.expires_at = datetime.now(timezone.utc) + timedelta(days=28)
        d.add(s)
    assert owner.get("/v1/workspaces").status_code == 200
    with db(owner) as d:
        s = d.exec(select(LoginSession)).one()
        assert s.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) + timedelta(days=29, hours=23)  # slid to 30 days
    with db(owner) as d, d.begin():
        s = d.exec(select(LoginSession)).one()
        s.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        d.add(s)
    assert owner.get("/v1/workspaces").status_code == 401
    assert owner.get("/v1/auth/session").json()["user"] is None


def test_cookie_requests_that_change_things_need_the_csrf_token(owner, files):
    body = {"changes": {"instructions/greeting.md": "Hi."}}
    csrf = owner.headers.pop(access.CSRF_HEADER)
    assert csrf == owner.cookies.get(access.CSRF_COOKIE) == csrf_token(owner.cookies.get(access.SESSION_COOKIE))
    res = owner.post(f"{WS}/files", json=body)
    assert res.status_code == 403 and "CSRF" in res.json()["detail"]
    assert owner.post(f"{WS}/files", json=body, headers={access.CSRF_HEADER: "0" * 64}).status_code == 403
    assert owner.post("/v1/auth/logout").status_code == 403
    assert owner.get(f"{WS}/files").status_code == 200  # reading needs none
    assert owner.post(f"{WS}/files", json=body, headers={access.CSRF_HEADER: csrf}).status_code == 200


def test_secure_cookies_on_https(app):
    c = TestClient(app, base_url="https://sopops.example")
    res = sign_in(c, "github", "1", "ana@example.com")
    assert all("Secure" in h for h in res.headers.get_list("set-cookie") if h.startswith(("sopops_session=", "sopops_csrf=")))
    assert any("HttpOnly" not in h for h in res.headers.get_list("set-cookie") if h.startswith("sopops_csrf="))  # the UI reads it


# --- roles -------------------------------------------------------------------------------


def join(owner: TestClient, app, email: str, role: str, id: str) -> TestClient:
    token = owner.post("/v1/org/invites", json={"email": email, "role": role}).json()["link"].split("#")[1]
    c = TestClient(app)
    sign_in(c, "github", id, email)
    assert c.get("/v1/auth/session").json()["org"]["role"] == role, token
    return c


def test_roles(owner, app):
    admin = join(owner, app, "adam@example.com", "admin", "10")
    member = join(owner, app, "mia@example.com", "member", "11")
    ids = {m["email"]: m["user_id"] for m in owner.get("/v1/org/members").json()}
    assert set(ids) == {"ana@example.com", "adam@example.com", "mia@example.com"}

    # members do everything in the app, but not the org
    assert member.get("/v1/org/members").status_code == 200
    assert member.post(f"{WS}/files", json={"changes": {"instructions/note.md": "Be kind."}}).status_code == 200
    assert member.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).status_code == 201
    for res in (member.post("/v1/org/invites", json={"email": "x@example.com"}), member.patch("/v1/org", json={"name": "Mine"}),
                member.get("/v1/org/invites"), member.delete(f"/v1/org/members/{ids['adam@example.com']}"),
                member.patch(f"/v1/org/members/{ids['mia@example.com']}", json={"role": "admin"})):
        assert res.status_code == 403, res.text

    # admins manage members and invites, but not owners
    assert admin.post("/v1/org/invites", json={"email": "x@example.com", "role": "admin"}).status_code == 201
    assert admin.post("/v1/org/invites", json={"email": "y@example.com", "role": "owner"}).status_code == 403
    assert admin.patch(f"/v1/org/members/{ids['mia@example.com']}", json={"role": "admin"}).json()["role"] == "admin"
    assert admin.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "member"}).status_code == 403
    assert admin.delete(f"/v1/org/members/{ids['ana@example.com']}").status_code == 403
    assert admin.patch("/v1/org", json={"name": "Pizza team"}).json()["name"] == "Pizza team"

    # the org keeps an owner
    assert owner.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "admin"}).status_code == 409
    assert owner.delete(f"/v1/org/members/{ids['ana@example.com']}").status_code == 409
    assert owner.patch(f"/v1/org/members/{ids['adam@example.com']}", json={"role": "owner"}).json()["role"] == "owner"
    assert owner.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "member"}).json()["role"] == "member"

    # removed: still signed in, but out of the org
    assert admin.delete(f"/v1/org/members/{ids['mia@example.com']}").status_code == 204
    assert member.get(f"{WS}/files").status_code == 404 and member.get("/v1/workspaces").status_code == 403


def test_workspaces_belong_to_the_org(owner, app, files):
    with db(owner) as d, d.begin():
        d.add(Org(id="other-org", name="Other", slug="other", allowed_domains=[], created_at=datetime.now(timezone.utc)))
        d.flush()
        d.add(Workspace(workspace="theirs", org_id="other-org"))
    assert [w["workspace"] for w in owner.get("/v1/workspaces").json()] == ["demo"]
    assert owner.get("/v1/workspaces/theirs/files").status_code == 404
    assert owner.put("/v1/workspaces/fresh/files", json={"files": files}).status_code == 200  # a new one is ours
    with db(owner) as d:
        mine = d.exec(select(Membership)).one().org_id
        assert d.get(Workspace, "fresh").org_id == mine


def test_signed_in_authors_are_recorded(owner, files):
    res = owner.post(f"{WS}/files", json={"changes": {"instructions/greeting.md": "Hello."}, "author": "Somebody else"})
    assert res.status_code == 200
    with db(owner) as d:
        ana = d.exec(select(User)).one()
        row = d.exec(select(FileVersion).where(FileVersion.path == "instructions/greeting.md")).one()
        assert (row.author, row.author_id) == ("Ana", ana.id)
    release = owner.post(f"{WS}/releases", json={"author": "nobody"}).json()["release"]
    assert release["author"] == "Ana"
    token = owner.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).json()
    assert token["created_by"] == "Ana"


# --- the route table --------------------------------------------------------------------


def routes(app) -> list[tuple[str, str, str]]:
    """(name, method, path) for every route in the app, the included routers' too."""
    out = []
    for r in app.routes:
        for x in getattr(r, "original_router", None) and r.original_router.routes or [r]:
            for m in sorted(getattr(x, "methods", None) or ["GET"]):
                if m != "HEAD":
                    out.append((x.name, m, x.path))
    return out


def url(path: str) -> str:
    return re.sub(r"\{workspace\}", "demo", re.sub(r"\{(\w+)(:path)?\}", lambda m: "1" if m.group(1) in ("number", "installation_id", "version") else "x", path))


EXPECTED = {
    access.OPEN: {"spa", "healthz", "openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html", "auth_session", "sign_in", "auth_callback",
                  "lookup_invite", "request_email_link", "confirm_email_link", "password_login", "first_account", "join_with_password",
                  "request_password_link", "lookup_password_link", "set_password", "lookup_join_link", "join_with_link", "onboarding_skill", "sign_up"},
    access.SIGNED_IN: {"sign_out", "accept_invite", "switch_org", "change_password", "accept_join_link", "create_team", "start_support_session",
                       "end_support_session", "get_support_state", "list_support_teams"},
    access.TOKEN: {"ingest_session", "open_session", "append_session_events", "close_session", "record_served", "onboarding_session",
                   "onboarding_progress", "onboarding_push", "onboarding_pull_request", "onboarding_open_pull_request", "onboarding_check",
                   "onboarding_kickoff", "onboarding_guide", "onboarding_extraction", "onboarding_project"},
    access.TOKEN_OR_SESSION: {"get_agent", "get_prompt", "get_sop", "get_config", "config_list_agents", "config_create_agent", "config_get_agent",
                              "config_put_agent", "config_delete_agent", "config_agent_history", "config_agent_version", "config_list_instructions",
                              "config_create_instruction", "config_get_instruction", "config_put_instruction", "config_delete_instruction",
                              "config_instruction_history", "config_instruction_version", "config_list_procedures", "config_create_procedure",
                              "config_get_procedure", "config_put_procedure", "config_delete_procedure", "config_procedure_history",
                              "config_procedure_version", "config_list_groups", "config_create_group", "config_get_group", "config_put_group",
                              "config_delete_group", "config_group_history", "config_group_version", "add_block", "remove_block", "preview_agent",
                              "get_settings", "put_settings", "settings_history", "settings_version"},
    access.WEBHOOK: {"github_webhook", "livekit_webhook", "elevenlabs_post_call", "elevenlabs_conversation_init", "vapi_assistant_request",
                     "provider_webhook"},
    access.ADMIN: {"update_org", "add_members", "set_member_role", "remove_member", "list_invites", "create_invite", "resend_invite", "revoke_invite",
                   "create_password_link", "github_app_manifest", "github_app_manifest_callback", "set_up_org", "get_join_link", "create_join_link",
                   "delete_join_link", "set_typesafe_key", "set_ai_provider", "set_ai_models", "reset_ai_setting",
                   "backfill_preview", "start_backfill", "get_backfill", "set_org_support"},
}


def test_every_route_is_in_the_table(app):
    names = {name for name, _, _ in routes(app)}
    assert names == set(access.ROUTES), (names - set(access.ROUTES), set(access.ROUTES) - names)
    for kind, expected in EXPECTED.items():  # all but `session`: changing these is a decision, so it's spelled out here
        assert {n for n, k in access.ROUTES.items() if k == kind} == expected, kind
    assert set(access.ROUTES.values()) <= set(access.KINDS)


def test_with_sign_in_on_every_session_route_needs_one(app):
    c = TestClient(app)
    for name, method, path in routes(app):
        res = c.request(method, url(path), follow_redirects=False)
        kind = access.ROUTES[name]
        guarded = res.status_code == 401 and res.json().get("detail") == "sign in first"
        if kind in (access.SESSION, access.ADMIN, access.SIGNED_IN):
            assert guarded, (name, method, res.status_code, res.text[:200])
        else:
            assert not guarded, (name, method)
        if kind == access.TOKEN_OR_SESSION:
            assert res.status_code == 401, (name, res.text)  # with sign-in on, prompt fetches always need the runtime token


def test_with_sign_in_off_nothing_needs_one(client, files):
    assert not mode.required()
    assert client.get("/v1/auth/session").json() == {"required": False, "providers": [], "user": None, "org": None, "orgs": [], "first_account": False,
                                                     "password_email": False, "sign_up": False, "open_signup": False,
                                                     "platforms": {"selected": None, "shown": ["livekit", "elevenlabs", "vapi"]}}
    for name, method, path in routes(client.app):
        if method != "GET" or name in ("stream_sessions", "import_stream") or access.ROUTES[name] == access.TOKEN:
            continue  # reading only (no side effects); streams don't end; token routes check their own token either way
        res = client.get(url(path), follow_redirects=False)
        assert res.status_code not in (401, 403), (name, res.status_code, res.text[:200])
    assert client.put(f"{WS}/files", json={"files": files, "author": "Ana"}).status_code == 200  # no CSRF either
    with Session(client.app.state.store.engine) as d:
        assert {(r.author, r.author_id) for r in d.exec(select(FileVersion))} == {("Ana", None)}
    assert client.get("/v1/org/members").status_code == 409  # there are no members to manage


def test_providers_register_and_plugins_can_add_one(app):
    class Saml(providers.LoginProvider):
        id, label = "saml", "Your company"

        def configured(self) -> bool:
            return True

        def authorize_url(self, redirect_uri, state, code_verifier):
            return f"https://idp.example/sso?state={state}"

        def profile(self, code, redirect_uri, code_verifier):
            return providers.Profile("saml", "u-1", "sam@example.com", "Sam")

    assert [p.id for p in providers.registered()][:2] == ["github", "google"]
    providers.register(Saml())
    try:
        c = TestClient(app)
        assert offered("saml", "Your company") in c.get("/v1/auth/session").json()["providers"]
        st = parse_qs(urlparse(c.get("/v1/auth/login/saml", follow_redirects=False).headers["location"]).query)["state"][0]
        assert c.get("/v1/auth/callback/saml", params={"code": "x", "state": st}, follow_redirects=False).status_code == 303
        assert c.get("/v1/auth/session").json()["user"]["email"] == "sam@example.com"
    finally:
        providers.unregister("saml")


class Plugin(providers.LoginProvider):
    """A provider a package registered, like one a plugin brings. It runs a session of its own and ends it when
    somebody signs out of sopops, which is the other half of what a plugin can do beyond saying who signed in."""

    id, label = "acme-sso", "Acme SSO"

    def __init__(self) -> None:
        self.ended: list[str] = []

    def configured(self) -> bool:
        return True

    def authorize_url(self, redirect_uri, state, code_verifier):
        return f"https://idp.example/sso?state={state}"

    def profile(self, code, redirect_uri, code_verifier):
        return providers.Profile(self.id, "u-7", "kim@example.com", "Kim", refresh_token="their-refresh-token")

    def end_session(self, refresh_token: str) -> bool:
        self.ended.append(refresh_token)
        return True


@pytest.fixture
def plugin():
    """Registered for the test and taken away again, as a package's provider would be by uninstalling it."""
    p = providers.register(Plugin())
    yield p
    providers.unregister(Plugin.id)


# --- which providers a deployment offers (SOPOPS_LOGIN_PROVIDERS) -------------------------


def test_the_allowlist_offers_what_it_names_and_nothing_else(app, plugin, monkeypatch):
    c = TestClient(app)
    assert {p["id"] for p in c.get("/v1/auth/session").json()["providers"]} == {"github", "google", "acme-sso", "password"}
    monkeypatch.setenv(providers.ALLOWLIST_ENV, "acme-sso")
    assert [p["id"] for p in c.get("/v1/auth/session").json()["providers"]] == ["acme-sso"]


def test_a_provider_left_out_is_closed_at_its_routes_too(app, plugin, monkeypatch):
    """Off the sign-in page is not enough: the login and callback routes for one left out answer 404."""
    monkeypatch.setenv(providers.ALLOWLIST_ENV, "acme-sso")
    c = TestClient(app)
    assert c.get("/v1/auth/login/github", follow_redirects=False).status_code == 404
    assert c.get("/v1/auth/login/acme-sso", follow_redirects=False).status_code == 302


def test_a_list_naming_nothing_configured_is_ignored_with_a_warning(app, monkeypatch, caplog):
    """A typo in the list would otherwise leave no way in at all, so it falls back to offering everything."""
    monkeypatch.setenv(providers.ALLOWLIST_ENV, "githb, saml")
    with caplog.at_level(logging.WARNING, logger="sopops.auth"):
        assert {p["id"] for p in TestClient(app).get("/v1/auth/session").json()["providers"]} == {"github", "google", "password"}
    assert "names no login provider that's set up here" in caplog.text


def test_an_id_that_is_registered_but_not_configured_is_skipped(app, monkeypatch):
    """`email` is registered always and configured only with SMTP: naming it alongside a provider that is set up
    leaves it out of the list rather than offering a button that can't work."""
    monkeypatch.setenv(providers.ALLOWLIST_ENV, "github, email")
    assert [p["id"] for p in TestClient(app).get("/v1/auth/session").json()["providers"]] == ["github"]


# --- a provider that runs its own session -------------------------------------------------


def test_signing_out_ends_the_providers_own_session_first(app, plugin, monkeypatch):
    """The provider's refresh token is kept on the login session, encrypted, and spent on the way out: without
    this, sopops' sign-out leaves their session alive and the next click walks straight back in."""
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    c = TestClient(app)
    st = parse_qs(urlparse(c.get("/v1/auth/login/acme-sso", follow_redirects=False).headers["location"]).query)["state"][0]
    assert c.get("/v1/auth/callback/acme-sso", params={"code": "x", "state": st}, follow_redirects=False).status_code == 303
    with Session(app.state.store.engine) as db:
        s = db.exec(select(LoginSession)).one()
        assert s.provider == "acme-sso" and s.refresh_ciphertext and "their-refresh-token" not in s.refresh_ciphertext
    c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    assert c.post("/v1/auth/logout").status_code == 204
    assert plugin.ended == ["their-refresh-token"]
    with Session(app.state.store.engine) as db:
        s = db.exec(select(LoginSession)).one()
        assert s.revoked_at is not None and s.refresh_ciphertext is None


def test_a_provider_that_raises_on_the_way_out_still_signs_somebody_out(app, plugin, monkeypatch, caplog):
    """A provider is somebody else's code. It must not be able to keep anybody signed in here."""
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(plugin, "end_session", lambda token: (_ for _ in ()).throw(RuntimeError("their server is down")))
    c = TestClient(app)
    st = parse_qs(urlparse(c.get("/v1/auth/login/acme-sso", follow_redirects=False).headers["location"]).query)["state"][0]
    c.get("/v1/auth/callback/acme-sso", params={"code": "x", "state": st}, follow_redirects=False)
    c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    with caplog.at_level(logging.ERROR, logger="sopops.auth"):
        assert c.post("/v1/auth/logout").status_code == 204
    with Session(app.state.store.engine) as db:
        assert db.exec(select(LoginSession)).one().revoked_at is not None


def test_a_session_from_a_provider_that_runs_none_keeps_nothing(app):
    """GitHub has no session of sopops' to end, so nothing is stored to end it with."""
    c = TestClient(app)
    sign_in(c, "github", "1", "ana@example.com")
    with Session(app.state.store.engine) as db:
        s = db.exec(select(LoginSession)).one()
        assert (s.provider, s.refresh_ciphertext) == ("github", None)


# --- token and webhook routes, with sign-in on --------------------------------------------


def test_token_and_webhook_routes_work_without_a_session(owner, app, files, monkeypatch):
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())  # platform secrets are stored encrypted
    assert owner.post(f"{WS}/releases").status_code == 200
    ingest = owner.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).json()["token"]
    runtime = owner.post(f"{WS}/tokens", json={"scopes": ["runtime"]}).json()["token"]
    assert owner.put(f"{WS}/runtime/platforms/elevenlabs/webhook-secret", json={"value": "el-hook-secret"}).status_code == 200
    agent = next(iter(owner.get(f"{WS}/agents").json()["agents"]))["agent"]

    agentside = TestClient(app)  # no cookie, no CSRF token
    session = {"platform": "livekit", "external_id": "room-1", "agent": agent, "started_at": "2026-10-01T18:00:00Z", "ended_at": "2026-10-01T18:01:00Z",
               "turns": [{"role": "assistant", "text": "Hi.", "at": 1759341601}]}
    assert agentside.post(f"{WS}/sessions", json=session, headers={"Authorization": f"Bearer {ingest}"}).status_code == 201
    assert agentside.post(f"{WS}/sessions", json=session).status_code == 401
    assert agentside.get(f"{WS}/agents/{agent}/prompt", headers={"Authorization": f"Bearer {runtime}"}).status_code == 200
    assert agentside.get(f"{WS}/agents/{agent}/prompt").status_code == 401
    assert owner.get(f"{WS}/agents/{agent}/prompt").status_code == 200  # someone signed in may look
    hook = agentside.post(f"{WS}/runtime/elevenlabs/conversation-init", params={"agent": agent}, json={}, headers={"X-SopOps-Secret": "el-hook-secret"})
    assert hook.status_code == 200
    assert agentside.post("/v1/github/webhook", content=b"{}", headers={"X-GitHub-Event": "ping"}).status_code in (401, 503)  # its own check

    # a cookie on a token route changes nothing: no CSRF token needed, and the bearer token is what counts
    assert owner.post(f"{WS}/sessions", json={**session, "external_id": "room-2"},
                      headers={"Authorization": f"Bearer {ingest}", access.CSRF_HEADER: ""}).status_code == 201


def test_the_onboarding_token_works_without_a_session(owner, app):
    started = owner.post(f"{WS}/imports/agent", json={"mode": "hosted", "server_url": "http://sopops.test"})
    assert started.status_code == 201, started.text
    token = started.json()["token"]
    assert TestClient(app).get("/v1/onboarding/session", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# --- the migration -----------------------------------------------------------------------


def test_the_migration_puts_existing_workspaces_in_one_org(tmp_path, files):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    store = Store(url, Sopc(SOPC_BIN))
    for ws in ("zeta", "alpha"):
        store.replace(ws, files)
    engine = create_engine(url)
    downgrade(engine, "0022")
    with engine.connect() as conn:
        assert "org_id" not in [c[1] for c in conn.execute(text("PRAGMA table_info(workspaces)"))]
    upgrade(engine)
    with Session(engine) as d:
        org = d.exec(select(Org)).one()
        assert (org.name, org.slug, org.allowed_domains) == ("alpha", "alpha", [])
        assert {w.workspace: w.org_id for w in d.exec(select(Workspace))} == {"alpha": org.id, "zeta": org.id}
        assert all(v.author_id is None for v in d.exec(select(FileVersion)))


def test_the_migration_on_an_empty_database_makes_a_default_org(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'e.db'}")
    upgrade(engine)
    with Session(engine) as d:
        assert [o.name for o in d.exec(select(Org))] == ["Default"]


def test_switching_orgs(owner, app):
    with db(owner) as d, d.begin():
        d.add(Org(id="second", name="Second", slug="second", allowed_domains=[], created_at=datetime.now(timezone.utc)))
        d.flush()
        d.add(Membership(org_id="second", user_id=d.exec(select(User)).one().id, role="member", created_at=datetime.now(timezone.utc)))
        d.add(Workspace(workspace="two", org_id="second"))
    assert len(owner.get("/v1/auth/session").json()["orgs"]) == 2
    switched = owner.put("/v1/auth/org", json={"org_id": "second"}).json()
    assert switched["org"]["id"] == "second" and switched["org"]["role"] == "member"
    assert [w["workspace"] for w in owner.get("/v1/workspaces").json()] == ["two"]
    assert owner.get(f"{WS}/files").status_code == 200  # still a member of the first
    assert owner.put("/v1/auth/org", json={"org_id": str(uuid.uuid4())}).status_code == 404


# --- email sign-in -------------------------------------------------------------------------

LINK = re.compile(r"/sign-in/email#([A-Za-z0-9_-]+)")


class FakeSMTP:
    """Stands in for smtplib.SMTP (and SMTP_SSL): records what a send did."""

    sent: list = []
    calls: list = []
    starttls_offered = True

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.calls.append(("connect", host, port, type(self).__name__))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        FakeSMTP.calls.append(("ehlo",))

    def has_extn(self, name):
        return name == "starttls" and FakeSMTP.starttls_offered

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user, password))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch):
    """SMTP set up, against FakeSMTP."""
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.starttls_offered = [], [], True
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    for k, v in {"SOPOPS_SMTP_HOST": "smtp.example.com", "SOPOPS_SMTP_USER": "mailer", "SOPOPS_SMTP_PASSWORD": "pw",
                 "SOPOPS_SMTP_FROM": "sopops <sopops@example.com>"}.items():
        monkeypatch.setenv(k, v)
    return FakeSMTP


@pytest.fixture
def dev_email(monkeypatch):
    monkeypatch.setenv("SOPOPS_EMAIL_DEV", "1")


def ask_link(c: TestClient, email: str, caplog, next: str = "/") -> str | None:
    """Ask for a sign-in link (dev mode) and read it from the log; None if none was logged."""
    caplog.clear()
    with caplog.at_level("INFO", logger="sopops.auth"):
        res = c.post("/v1/auth/email", json={"email": email, "next": next})
    assert res.status_code == 202 and res.json() == {"message": "If that address can sign in, we sent a link. It works for 15 minutes."}
    found = [m for m in caplog.messages if "[SOPOPS_EMAIL_DEV]" in m]
    return LINK.search(found[-1]).group(1) if found else None


def confirm(c: TestClient, token: str) -> httpx.Response:
    res = c.post("/v1/auth/email/confirm", json={"token": token})
    if c.cookies.get(access.CSRF_COOKIE):
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    return res


def test_email_sign_in_turns_sign_in_on(monkeypatch, dev_email):
    assert mode.required() and [p.id for p in providers.configured()] == ["email", "password"]
    monkeypatch.delenv("SOPOPS_EMAIL_DEV")
    assert not mode.required()
    monkeypatch.setenv("SOPOPS_SMTP_HOST", "smtp.example.com")
    assert not mode.required()  # a host but no sender
    monkeypatch.setenv("SOPOPS_SMTP_FROM", "sopops@example.com")
    assert mode.required()


def test_an_email_link_signs_in_once(tmp_path, files, dev_email, caplog):
    app = create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))
    c = TestClient(app)
    assert c.get("/v1/auth/session").json()["providers"] == [offered("email", "Email", redirects=False), offered("password", "Password", redirects=False)]
    assert c.get("/v1/auth/login/email", follow_redirects=False).status_code == 404  # it doesn't redirect
    token = ask_link(c, "Ana@Example.com", caplog, next="/w/demo/sessions")
    assert token and "ana@example.com" in caplog.text
    with db(c) as d:
        row = d.exec(select(EmailLink)).one()
        assert row.token_hash != token and token not in json.dumps(row.model_dump(), default=str)  # stored hashed
        assert row.expires_at.replace(tzinfo=timezone.utc) - row.created_at.replace(tzinfo=timezone.utc) == timedelta(minutes=15)

    res = confirm(c, token)
    assert res.status_code == 200 and res.json()["next"] == "/w/demo/sessions"
    assert res.json()["user"]["email"] == "ana@example.com" and res.json()["org"]["role"] == "owner"  # the first user owns the org
    assert any(h.startswith("sopops_session=") and "HttpOnly" in h for h in res.headers.get_list("set-cookie"))
    assert c.get("/v1/workspaces").status_code == 200
    with db(c) as d:
        assert [(i.provider, i.provider_user_id) for i in d.exec(select(Identity))] == [("email", "ana@example.com")]

    again = TestClient(app)
    assert confirm(again, token).status_code == 404  # single use
    assert again.get("/v1/auth/session").json()["user"] is None
    assert confirm(again, "made-up").status_code == 404


def test_using_a_link_uses_up_the_others_and_links_expire(app, dev_email, caplog):
    c = TestClient(app)
    first, second = ask_link(c, "bo@example.com", caplog), ask_link(c, "bo@example.com", caplog)
    assert confirm(c, second).status_code == 200
    assert confirm(TestClient(app), first).status_code == 404
    late = ask_link(c, "cy@example.com", caplog)
    with db(c) as d, d.begin():
        row = d.exec(select(EmailLink).where(EmailLink.email == "cy@example.com")).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        d.add(row)
    res = confirm(TestClient(app), late)
    assert res.status_code == 404 and "expired" in res.json()["detail"]


def test_email_links_are_rate_limited(app, dev_email, caplog):
    c = TestClient(app)
    assert all(ask_link(c, "dee@example.com", caplog) for _ in range(5))
    assert ask_link(c, "dee@example.com", caplog) is None  # the 6th in 15 minutes: the same answer, no link
    with db(c) as d, d.begin():
        for row in d.exec(select(EmailLink)):
            row.created_at -= timedelta(minutes=16)
            d.add(row)
    assert ask_link(c, "dee@example.com", caplog)  # the window moved on
    for i in range(19):  # 20 per client in 15 minutes, whatever the address
        assert ask_link(c, f"p{i}@example.com", caplog), i
    assert ask_link(c, "new@example.com", caplog) is None


def test_the_answer_is_the_same_for_any_address(owner, app, dev_email, caplog, monkeypatch):
    c = TestClient(app)
    known = c.post("/v1/auth/email", json={"email": "ana@example.com"})
    unknown = c.post("/v1/auth/email", json={"email": "nobody@unknown.example"})
    monkeypatch.setenv("SOPOPS_ALLOWED_EMAIL_DOMAINS", "example.com")
    with caplog.at_level("INFO", logger="sopops.auth"):
        caplog.clear()
        refused = c.post("/v1/auth/email", json={"email": "eve@elsewhere.example"})
    assert not any("[SOPOPS_EMAIL_DEV]" in m for m in caplog.messages)  # no link for a domain this sopops doesn't take
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json()) == (refused.status_code, refused.json())
    assert c.post("/v1/auth/email", json={"email": "not an address"}).status_code == 400


def test_an_email_link_signs_in_the_github_or_google_user_with_that_email(owner, app, dev_email, caplog):
    with db(owner) as d:
        ana = d.exec(select(User)).one()
    c = TestClient(app)
    confirm(c, ask_link(c, "ANA@example.com", caplog))
    me = c.get("/v1/auth/session").json()
    assert me["user"]["id"] == ana.id and me["user"]["name"] == "Ana" and me["org"]["role"] == "owner"
    with db(owner) as d:
        assert sorted(i.provider for i in d.exec(select(Identity).where(Identity.user_id == ana.id))) == ["email", "github"]
        assert len(d.exec(select(User)).all()) == 1
    g = TestClient(app)  # and the other way: Google after email
    confirm(g, ask_link(g, "gus@example.com", caplog))
    gus = g.get("/v1/auth/session").json()["user"]["id"]
    sign_in(g, "google", "g-gus", "gus@example.com")
    assert g.get("/v1/auth/session").json()["user"]["id"] == gus


def test_an_email_link_follows_the_join_rules(owner, app, dev_email, caplog):
    stranger = TestClient(app)
    confirm(stranger, ask_link(stranger, "zed@elsewhere.example", caplog))
    assert stranger.get("/v1/auth/session").json()["org"] is None  # "Ask an owner to invite you"
    owner.post("/v1/org/invites", json={"email": "ivy@example.com", "role": "admin"})
    ivy = TestClient(app)
    confirm(ivy, ask_link(ivy, "ivy@example.com", caplog))
    assert ivy.get("/v1/auth/session").json()["org"]["role"] == "admin"
    owner.patch("/v1/org", json={"allowed_domains": ["team.example"]})
    tam = TestClient(app)
    confirm(tam, ask_link(tam, "tam@team.example", caplog))
    assert tam.get("/v1/auth/session").json()["org"]["role"] == "member"


def test_links_are_emailed_and_never_logged_without_dev_mode(app, smtp, caplog):
    c = TestClient(app)
    with caplog.at_level("DEBUG"):
        res = c.post("/v1/auth/email", json={"email": "ana@example.com"})
    assert res.status_code == 202
    (msg,) = smtp.sent
    assert msg["Subject"] == "Your sopops sign-in link" and msg["To"] == "ana@example.com" and msg["From"] == "sopops <sopops@example.com>"
    text_part = msg.get_body(("plain",)).get_content()
    html_part = msg.get_body(("html",)).get_content()
    token = LINK.search(text_part).group(1)
    assert f"http://testserver/sign-in/email#{token}" in text_part and token in html_part
    assert "15 minutes" in text_part and "ignore this email" in text_part and "—" not in text_part
    assert token not in caplog.text and "SOPOPS_EMAIL_DEV" not in caplog.text
    assert smtp.calls == [("connect", "smtp.example.com", 587, "FakeSMTP"), ("ehlo",), ("starttls",), ("ehlo",), ("login", "mailer", "pw")]
    assert confirm(c, token).status_code == 200


def test_smtp_ports_and_tls(app, smtp, monkeypatch):
    msg = mail.Message(to="x@example.com", subject="s", text="t", html="<p>t</p>")
    monkeypatch.setenv("SOPOPS_SMTP_PORT", "465")
    mail.send(msg)
    assert smtp.calls[0] == ("connect", "smtp.example.com", 465, "FakeSMTPSSL") and ("starttls",) not in smtp.calls
    monkeypatch.setenv("SOPOPS_SMTP_PORT", "25")
    smtp.calls.clear()
    smtp.starttls_offered = False
    with pytest.raises(mail.MailError, match="won't send the SMTP password unencrypted"):
        mail.send(msg)
    assert not any(c[0] == "login" for c in smtp.calls)


def test_dev_mode_logs_instead_of_sending(app, smtp, dev_email, caplog):
    c = TestClient(app)
    assert ask_link(c, "ana@example.com", caplog)
    assert smtp.sent == []  # dev mode wins: nothing is sent, even with SMTP set up
    assert c.get("/v1/org").status_code == 401


# --- adding people -------------------------------------------------------------------------


def test_adding_people_with_and_without_accounts(owner, app, dev_email, caplog):
    bob = TestClient(app)
    sign_in(bob, "github", "2", "bob@example.com")  # has an account, in no org
    added = owner.post("/v1/org/members", json={"emails": ["Bob@example.com", "new@example.com", "ana@example.com", "new@example.com"], "role": "admin"})
    assert added.status_code == 200, added.text
    by = {a["email"]: a for a in added.json()}
    assert list(by) == ["bob@example.com", "new@example.com", "ana@example.com"]
    assert by["bob@example.com"]["status"] == "added" and by["ana@example.com"]["status"] == "already"
    inv = by["new@example.com"]["invite"]
    assert by["new@example.com"]["status"] == "invited" and inv["link"].startswith("http://testserver/invite#") and inv["emailed"] is False
    assert bob.get("/v1/auth/session").json()["org"]["role"] == "admin"  # joined at once
    assert owner.get("/v1/org").json()["sends_email"] is False  # dev mode doesn't send invites

    assert [i["email"] for i in owner.get("/v1/org/invites").json()] == ["new@example.com"]  # pending
    new = TestClient(app)
    confirm(new, ask_link(new, "new@example.com", caplog))  # signing in any way joins
    assert new.get("/v1/auth/session").json()["org"]["role"] == "admin"
    assert owner.get("/v1/org/invites").json() == []
    assert owner.post("/v1/org/members", json={"emails": ["nope"]}).status_code == 400


def test_invites_are_emailed_with_smtp(owner, app, smtp):
    assert owner.get("/v1/org").json()["sends_email"] is True
    made = owner.post("/v1/org/invites", json={"email": "kai@example.com"}).json()
    assert made["emailed"] is True and made["link"]
    (msg,) = smtp.sent
    assert msg["Subject"] == "Ana invited you to Default on sopops" and msg["To"] == "kai@example.com"
    assert made["link"] in msg.get_body(("plain",)).get_content()
    added = owner.post("/v1/org/members", json={"emails": ["lu@example.com"]}).json()
    assert added[0]["invite"]["emailed"] is True and len(smtp.sent) == 2


def test_resend_and_cancel_pending_invites(owner, app, smtp):
    first = owner.post("/v1/org/invites", json={"email": "max@example.com"}).json()
    again = owner.post(f"/v1/org/invites/{first['id']}/resend")
    assert again.status_code == 200 and again.json()["emailed"] and again.json()["link"] != first["link"]
    anon = TestClient(app)
    assert anon.post("/v1/auth/invites/lookup", json={"token": first["link"].split("#")[1]}).status_code == 404  # the old link stops working
    assert anon.post("/v1/auth/invites/lookup", json={"token": again.json()["link"].split("#")[1]}).status_code == 200
    replaced = owner.post("/v1/org/invites", json={"email": "max@example.com", "role": "admin"}).json()  # a new invite replaces the open one
    assert [(i["email"], i["role"]) for i in owner.get("/v1/org/invites").json()] == [("max@example.com", "admin")]
    assert owner.delete(f"/v1/org/invites/{replaced['id']}").status_code == 204
    assert owner.get("/v1/org/invites").json() == []
    assert owner.post(f"/v1/org/invites/{replaced['id']}/resend").status_code == 404


def test_who_can_add_people_and_change_roles(owner, app):
    admin = join(owner, app, "adam@example.com", "admin", "10")
    member = join(owner, app, "mia@example.com", "member", "11")
    ids = {m["email"]: m["user_id"] for m in owner.get("/v1/org/members").json()}

    assert member.post("/v1/org/members", json={"emails": ["x@example.com"]}).status_code == 403
    assert member.post(f"/v1/org/invites/{uuid.uuid4()}/resend").status_code == 403
    assert admin.post("/v1/org/members", json={"emails": ["x@example.com"], "role": "owner"}).status_code == 403  # only an owner makes owners
    assert admin.post("/v1/org/members", json={"emails": ["x@example.com"], "role": "admin"}).json()[0]["status"] == "invited"
    assert admin.patch(f"/v1/org/members/{ids['mia@example.com']}", json={"role": "owner"}).status_code == 403
    assert admin.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "admin"}).status_code == 403  # an admin can't change an owner
    assert admin.delete(f"/v1/org/members/{ids['ana@example.com']}").status_code == 403
    assert owner.post("/v1/org/members", json={"emails": ["oz@example.com"], "role": "owner"}).json()[0]["status"] == "invited"

    # the last owner can't be demoted or removed, by anyone
    assert owner.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "member"}).status_code == 409
    assert owner.delete(f"/v1/org/members/{ids['ana@example.com']}").status_code == 409
    assert owner.patch(f"/v1/org/members/{ids['mia@example.com']}", json={"role": "owner"}).json()["role"] == "owner"
    assert member.patch(f"/v1/org/members/{ids['ana@example.com']}", json={"role": "member"}).json()["role"] == "member"  # mia, an owner now
    assert member.delete(f"/v1/org/members/{ids['mia@example.com']}").status_code == 409  # she's the last one now


# --- passwords -----------------------------------------------------------------------------

PW = "correct horse battery"
PW_LINK = re.compile(r"/set-password#([A-Za-z0-9_-]+)")


@pytest.fixture
def pw_app(tmp_path, monkeypatch, files):
    """Sign-in forced on with no provider and no email: passwords only."""
    monkeypatch.setenv("SOPOPS_AUTH_REQUIRED", "true")
    app = create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))
    app.state.store.replace("demo", files)
    return app


def with_csrf(c: TestClient, res: httpx.Response) -> httpx.Response:
    if c.cookies.get(access.CSRF_COOKIE):
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    return res


def pw_login(c: TestClient, email: str, password: str = PW, next: str = "/") -> httpx.Response:
    return with_csrf(c, c.post("/v1/auth/password/login", json={"email": email, "password": password, "next": next}))


@pytest.fixture
def pw_owner(pw_app) -> TestClient:
    c = TestClient(pw_app)
    res = with_csrf(c, c.post("/v1/auth/password/first-account", json={"email": "ana@example.com", "name": "Ana", "password": PW}))
    assert res.status_code == 200, res.text
    return c


def pw_member(owner: TestClient, app, email: str, role: str = "member", password: str = PW) -> TestClient:
    token = owner.post("/v1/org/invites", json={"email": email, "role": role}).json()["link"].split("#")[1]
    c = TestClient(app)
    res = with_csrf(c, c.post("/v1/auth/invites/join", json={"token": token, "name": email.split("@")[0].title(), "password": password}))
    assert res.status_code == 200, res.text
    return c


def ids(owner: TestClient) -> dict[str, str]:
    return {m["email"]: m["user_id"] for m in owner.get("/v1/org/members").json()}


def test_passwords_alone_and_the_first_account(pw_app):
    c = TestClient(pw_app)
    state = c.get("/v1/auth/session").json()
    assert state["required"] and state["providers"] == [offered("password", "Password", redirects=False)]
    assert state["first_account"] and not state["password_email"] and not state["sign_up"]
    for bad, why in (("short", "at least 10"), ("x" * 257, "at most 256"), ("Ana@Example.com", "can't be your email")):
        res = c.post("/v1/auth/password/first-account", json={"email": "ana@example.com", "password": bad})
        assert res.status_code == 400 and why in res.json()["detail"], res.text
    res = with_csrf(c, c.post("/v1/auth/password/first-account", json={"email": "Ana@Example.com", "name": "Ana", "password": PW}))
    assert res.status_code == 200 and res.json()["org"]["role"] == "owner" and res.json()["user"]["has_password"]
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["demo"]
    with db(c) as d:
        ident = d.exec(select(Identity)).one()
        assert ident.provider == "password" and ident.password_hash.startswith("$argon2id$") and PW not in ident.password_hash

    # sign-up is closed now: no second first account, and nothing to sign up with
    anon = TestClient(pw_app)
    assert not anon.get("/v1/auth/session").json()["first_account"]
    res = anon.post("/v1/auth/password/first-account", json={"email": "eve@example.com", "name": "Eve", "password": PW})
    assert res.status_code == 409 and "already made the first account" in res.json()["detail"]
    assert anon.post("/v1/auth/password/email", json={"email": "eve@example.com"}).status_code == 404  # no email: no sign-up, no forgot password
    with db(c) as d:
        assert [u.email for u in d.exec(select(User))] == ["ana@example.com"]


def test_password_sign_in_and_the_same_answer_for_any_mistake(pw_owner, pw_app):
    c = TestClient(pw_app)
    wrong = c.post("/v1/auth/password/login", json={"email": "ana@example.com", "password": "not the password"})
    unknown = c.post("/v1/auth/password/login", json={"email": "nobody@example.com", "password": PW})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "That email and password don't match."}
    assert c.get("/v1/workspaces").status_code == 401
    res = pw_login(c, " ANA@example.com ", next="/w/demo/sessions")
    assert res.status_code == 200 and res.json()["next"] == "/w/demo/sessions" and res.json()["user"]["email"] == "ana@example.com"
    assert any(h.startswith("sopops_session=") and "HttpOnly" in h for h in res.headers.get_list("set-cookie"))
    assert c.get("/v1/workspaces").status_code == 200
    form = TestClient(pw_app).post("/v1/auth/password/login", content=f"email=ana@example.com&password={PW}",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert form.status_code == 422  # JSON only: another site's form can't sign a browser in


def test_password_sign_in_is_rate_limited(pw_owner, pw_app):
    c = TestClient(pw_app)
    for _ in range(10):
        assert c.post("/v1/auth/password/login", json={"email": "ana@example.com", "password": "wrong guess!"}).status_code == 401
    res = c.post("/v1/auth/password/login", json={"email": "ana@example.com", "password": PW})
    assert res.status_code == 429 and "Wait 15 minutes" in res.json()["detail"]  # even the right password, for now
    with db(c) as d, d.begin():
        for row in d.exec(select(LoginAttempt)):
            row.created_at -= timedelta(minutes=16)
            d.add(row)
    assert pw_login(c, "ana@example.com").status_code == 200  # the window moved on
    with db(c) as d:
        assert d.exec(select(LoginAttempt)).all() == []  # a match clears the address's tries
    other = TestClient(pw_app)  # 30 per client, whatever the address
    for i in range(30):
        assert other.post("/v1/auth/password/login", json={"email": f"p{i}@example.com", "password": PW}).status_code == 401
    assert other.post("/v1/auth/password/login", json={"email": "ana@example.com", "password": PW}).status_code == 429


def test_an_invite_joins_with_a_password(pw_owner, pw_app):
    made = pw_owner.post("/v1/org/invites", json={"email": "bo@example.com", "role": "admin"}).json()
    token = made["link"].split("#")[1]
    anon = TestClient(pw_app)
    info = anon.post("/v1/auth/invites/lookup", json={"token": token}).json()
    assert info["email"] == "bo@example.com" and info["has_account"] is False
    assert anon.post("/v1/auth/invites/join", json={"token": token, "name": "Bo", "password": "short"}).status_code == 400
    bo = TestClient(pw_app)
    res = with_csrf(bo, bo.post("/v1/auth/invites/join", json={"token": token, "name": "Bo", "password": PW}))
    assert res.status_code == 200 and res.json()["org"]["role"] == "admin" and res.json()["user"]["name"] == "Bo"
    assert bo.get("/v1/workspaces").status_code == 200
    assert pw_owner.get("/v1/org/invites").json() == []
    again = TestClient(pw_app).post("/v1/auth/invites/join", json={"token": token, "name": "Mallory", "password": "mallory's password"})
    assert again.status_code == 404  # used
    assert pw_login(TestClient(pw_app), "bo@example.com").status_code == 200

    # expired invites don't work
    late = pw_owner.post("/v1/org/invites", json={"email": "late@example.com"}).json()
    with db(pw_owner) as d, d.begin():
        inv = d.get(Invite, late["id"])
        inv.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        d.add(inv)
    assert TestClient(pw_app).post("/v1/auth/invites/join", json={"token": late["link"].split("#")[1], "password": PW}).status_code == 404


def test_an_invite_cant_set_a_password_on_an_existing_account(owner, app):
    """Bob has a GitHub account; an owner of another org invites him. The link can't be used to give his account a
    password, or anyone holding it could take the account over."""
    bob = TestClient(app)
    sign_in(bob, "github", "2", "bob@example.com")
    token = owner.post("/v1/org/invites", json={"email": "bob@example.com"}).json()["link"].split("#")[1]
    anon = TestClient(app)
    assert anon.post("/v1/auth/invites/lookup", json={"token": token}).json()["has_account"] is True
    res = anon.post("/v1/auth/invites/join", json={"token": token, "name": "Not Bob", "password": PW})
    assert res.status_code == 409 and "already has an account. Sign in to join" in res.json()["detail"]
    assert pw_login(TestClient(app), "bob@example.com").status_code == 401
    with db(owner) as d:
        assert d.exec(select(Identity).where(Identity.provider == "password")).all() == []
    assert anon.post("/v1/auth/password/first-account", json={"email": "bob@example.com", "password": PW}).status_code == 409


def test_a_reset_link_from_an_admin(pw_owner, pw_app):
    mia = pw_member(pw_owner, pw_app, "mia@example.com")
    laptop = TestClient(pw_app)
    pw_login(laptop, "mia@example.com")
    made = pw_owner.post(f"/v1/org/members/{ids(pw_owner)['mia@example.com']}/password-link")
    assert made.status_code == 201 and made.json()["email"] == "mia@example.com"
    link = made.json()["link"]
    assert link.startswith("http://testserver/set-password#")
    token = link.split("#")[1]
    expires = datetime.fromisoformat(made.json()["expires_at"])
    assert timedelta(minutes=59) < expires - datetime.now(timezone.utc) <= timedelta(hours=1)
    with db(pw_owner) as d:
        row = d.exec(select(PasswordLink)).one()
        assert row.token_hash != token and token not in json.dumps(row.model_dump(), default=str)  # stored hashed

    anon = TestClient(pw_app)
    assert anon.post("/v1/auth/password/link", json={"token": token}).json()["has_account"] is True
    assert anon.post("/v1/auth/password/set", json={"token": token, "password": "mia@example.com"}).status_code == 400  # not used up by a bad password
    res = with_csrf(anon, anon.post("/v1/auth/password/set", json={"token": token, "password": "a brand new password"}))
    assert res.status_code == 200 and res.json()["user"]["email"] == "mia@example.com"
    assert anon.get("/v1/workspaces").status_code == 200
    assert mia.get("/v1/workspaces").status_code == 401 and laptop.get("/v1/workspaces").status_code == 401  # her other sessions ended
    assert pw_login(TestClient(pw_app), "mia@example.com").status_code == 401  # the old password is gone
    assert pw_login(TestClient(pw_app), "mia@example.com", "a brand new password").status_code == 200
    again = TestClient(pw_app).post("/v1/auth/password/set", json={"token": token, "password": "yet another password"})
    assert again.status_code == 404 and "used already" in again.json()["detail"]  # single use

    late = pw_owner.post(f"/v1/org/members/{ids(pw_owner)['mia@example.com']}/password-link").json()["link"].split("#")[1]
    with db(pw_owner) as d, d.begin():
        for row in d.exec(select(PasswordLink).where(PasswordLink.used_at.is_(None))):
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            d.add(row)
    assert TestClient(pw_app).post("/v1/auth/password/link", json={"token": late}).status_code == 404
    assert TestClient(pw_app).post("/v1/auth/password/set", json={"token": late, "password": "yet another password"}).status_code == 404


def test_who_can_make_reset_links(pw_owner, pw_app):
    admin = pw_member(pw_owner, pw_app, "adam@example.com", "admin")
    member = pw_member(pw_owner, pw_app, "mia@example.com")
    who = ids(pw_owner)
    assert member.post(f"/v1/org/members/{who['adam@example.com']}/password-link").status_code == 403
    res = admin.post(f"/v1/org/members/{who['ana@example.com']}/password-link")
    assert res.status_code == 403 and "only an owner" in res.json()["detail"]  # an admin can't act on an owner
    assert admin.post(f"/v1/org/members/{who['mia@example.com']}/password-link").status_code == 201
    assert pw_owner.post(f"/v1/org/members/{who['adam@example.com']}/password-link").status_code == 201
    assert pw_owner.post(f"/v1/org/members/{uuid.uuid4()}/password-link").status_code == 404
    csrf = admin.headers.pop(access.CSRF_HEADER)
    assert admin.post(f"/v1/org/members/{who['mia@example.com']}/password-link").status_code == 403  # needs the CSRF token
    admin.headers[access.CSRF_HEADER] = csrf

    # someone also in an org the admin doesn't run: not theirs to reset
    with db(pw_owner) as d, d.begin():
        d.add(Org(id="other-org", name="Other", slug="other", allowed_domains=[], created_at=datetime.now(timezone.utc)))
        d.flush()
        d.add(Membership(org_id="other-org", user_id=who["mia@example.com"], role="member", created_at=datetime.now(timezone.utc)))
    res = admin.post(f"/v1/org/members/{who['mia@example.com']}/password-link")
    assert res.status_code == 403 and "also in another org" in res.json()["detail"]


def test_the_cli_prints_a_reset_link(pw_owner, pw_app, capsys, monkeypatch):
    url = pw_app.state.store.engine.url.render_as_string(hide_password=False)  # str() masks a Postgres password
    cli.main(["reset-password", "ANA@example.com", "--database-url", url, "--url", "https://sopops.example/"])
    out = capsys.readouterr().out
    token = PW_LINK.search(out).group(1)
    assert "https://sopops.example/set-password#" in out and "ana@example.com" in out
    c = TestClient(pw_app)
    assert with_csrf(c, c.post("/v1/auth/password/set", json={"token": token, "password": "the owner's new one"})).json()["org"]["role"] == "owner"
    assert pw_owner.get("/v1/workspaces").status_code == 401  # the owner's other session ended
    assert TestClient(pw_app).post("/v1/auth/password/set", json={"token": token, "password": "and another one"}).status_code == 404
    with pytest.raises(SystemExit, match="no account for nobody@example.com"):
        cli.main(["reset-password", "nobody@example.com", "--database-url", url])


def test_changing_your_password_ends_your_other_sessions(pw_owner, pw_app):
    phone = TestClient(pw_app)
    pw_login(phone, "ana@example.com")
    assert pw_owner.get("/v1/auth/session").json()["user"]["has_password"]
    res = pw_owner.post("/v1/auth/password/change", json={"current_password": "not it at all", "password": "a new password here"})
    assert res.status_code == 401 and "current password" in res.json()["detail"]
    assert pw_owner.post("/v1/auth/password/change", json={"current_password": PW, "password": "short"}).status_code == 400
    csrf = pw_owner.headers.pop(access.CSRF_HEADER)
    assert pw_owner.post("/v1/auth/password/change", json={"current_password": PW, "password": "a new password here"}).status_code == 403
    pw_owner.headers[access.CSRF_HEADER] = csrf
    assert pw_owner.post("/v1/auth/password/change", json={"current_password": PW, "password": "a new password here"}).status_code == 204
    assert pw_owner.get("/v1/workspaces").status_code == 200  # this one stays
    assert phone.get("/v1/workspaces").status_code == 401  # the others end
    assert pw_login(TestClient(pw_app), "ana@example.com").status_code == 401
    assert pw_login(TestClient(pw_app), "ana@example.com", "a new password here").status_code == 200
    assert TestClient(pw_app).post("/v1/auth/password/change", json={"current_password": PW, "password": "whatever it is"}).status_code == 401


def test_a_github_user_has_no_password_to_change(owner):
    assert owner.get("/v1/auth/session").json()["user"]["has_password"] is False
    res = owner.post("/v1/auth/password/change", json={"current_password": "anything at all", "password": "a new password here"})
    assert res.status_code == 400 and "sign in another way" in res.json()["detail"]


def test_email_options_only_with_email_set_up(pw_owner, pw_app, monkeypatch, caplog):
    state = TestClient(pw_app).get("/v1/auth/session").json()
    assert [p["id"] for p in state["providers"]] == ["password"] and not state["password_email"]  # no magic link without SMTP
    assert TestClient(pw_app).post("/v1/auth/email", json={"email": "ana@example.com"}).status_code == 404
    monkeypatch.setenv("SOPOPS_EMAIL_DEV", "1")
    state = TestClient(pw_app).get("/v1/auth/session").json()
    assert [p["id"] for p in state["providers"]] == ["email", "password"] and state["password_email"] and not state["sign_up"]


def ask_password_link(c: TestClient, email: str, caplog) -> str | None:
    caplog.clear()
    with caplog.at_level("INFO", logger="sopops.auth"):
        res = c.post("/v1/auth/password/email", json={"email": email})
    assert res.status_code == 202 and "we sent a link to set one" in res.json()["message"]
    found = [m for m in caplog.messages if "[SOPOPS_EMAIL_DEV]" in m]
    return PW_LINK.search(found[-1]).group(1) if found else None


def test_forgot_password_by_email(pw_owner, pw_app, dev_email, caplog):
    c = TestClient(pw_app)
    assert ask_password_link(c, "nobody@example.com", caplog) is None  # no account, and sign-up is closed: the same answer
    token = ask_password_link(c, "ana@example.com", caplog)
    assert token and c.post("/v1/auth/password/link", json={"token": token}).json()["has_account"]
    res = with_csrf(c, c.post("/v1/auth/password/set", json={"token": token, "password": "remembered it now"}))
    assert res.status_code == 200 and res.json()["org"]["role"] == "owner"
    assert pw_owner.get("/v1/workspaces").status_code == 401
    for _ in range(4):
        assert ask_password_link(c, "ana@example.com", caplog)
    assert ask_password_link(c, "ana@example.com", caplog) is None  # 5 per address in 15 minutes


def test_forgot_password_emails_with_smtp(pw_owner, pw_app, smtp):
    assert TestClient(pw_app).post("/v1/auth/password/email", json={"email": "ana@example.com"}).status_code == 202
    (msg,) = smtp.sent
    text_part = msg.get_body(("plain",)).get_content()
    assert msg["Subject"] == "Reset your sopops password" and "an hour" in text_part and "—" not in text_part
    assert PW_LINK.search(text_part)


def test_sign_up_at_allowed_domains_by_email_link(pw_owner, pw_app, dev_email, caplog, monkeypatch):
    monkeypatch.setenv("SOPOPS_ALLOWED_EMAIL_DOMAINS", "example.com,team.example")
    pw_owner.patch("/v1/org", json={"allowed_domains": ["team.example"]})
    c = TestClient(pw_app)
    assert c.get("/v1/auth/session").json()["sign_up"]
    assert ask_password_link(c, "eve@elsewhere.example", caplog) is None  # not an allowed domain
    token = ask_password_link(c, "tam@team.example", caplog)
    info = c.post("/v1/auth/password/link", json={"token": token}).json()
    assert info == {**info, "email": "tam@team.example", "has_account": False}
    res = with_csrf(c, c.post("/v1/auth/password/set", json={"token": token, "name": "Tam", "password": "tam's own password"}))
    assert res.status_code == 200 and res.json()["user"]["name"] == "Tam" and res.json()["org"]["role"] == "member"  # the org takes the domain
    assert pw_login(TestClient(pw_app), "tam@team.example", "tam's own password").status_code == 200


def test_passwords_are_off_with_sign_in_off(client):
    assert client.post("/v1/auth/password/login", json={"email": "ana@example.com", "password": PW}).status_code == 404
    assert client.post("/v1/auth/password/first-account", json={"email": "ana@example.com", "password": PW}).status_code == 404


def test_a_plugin_can_take_passwords_away(pw_owner, pw_app):
    taken = providers.get("password")
    providers.unregister("password")
    try:
        assert pw_login(TestClient(pw_app), "ana@example.com").status_code == 404
        assert TestClient(pw_app).get("/v1/auth/session").json()["providers"] == []
    finally:
        providers.register(taken)


def test_the_password_migration_goes_down_and_up(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    upgrade(engine)
    downgrade(engine, "0024")
    with engine.connect() as conn:
        assert "password_hash" not in [c[1] for c in conn.execute(text("PRAGMA table_info(identities)"))]
    upgrade(engine)
    with engine.connect() as conn:
        assert "password_hash" in [c[1] for c in conn.execute(text("PRAGMA table_info(identities)"))]


def test_a_config_token_edits_the_forms_but_cannot_publish(owner, app, files):
    """A coding agent keeps a workspace's SOPs up to date with a `config` token: it reads and saves drafts, and
    publishing stays with people."""
    config = owner.post(f"{WS}/tokens", json={"scopes": ["config"]}).json()["token"]
    ingest = owner.post(f"{WS}/tokens", json={"scopes": ["ingest"]}).json()["token"]
    agentside = TestClient(app)  # no cookie, no CSRF token
    auth = {"Authorization": f"Bearer {config}"}

    got = agentside.get(f"{WS}/config/procedures", headers=auth)
    assert got.status_code == 200
    one = got.json()[0]
    edited = {**one, "goal": "Confirm the order before closing."}
    assert agentside.put(f"{WS}/config/procedures/{one['id']}", json=edited, headers=auth).status_code == 200
    assert agentside.get(f"{WS}/config/procedures/{one['id']}", headers=auth).json()["goal"] == "Confirm the order before closing."

    # the draft reached head, and nothing was published
    assert agentside.put(f"{WS}/config/procedures/{one['id']}", json=edited).status_code == 401       # no token
    assert agentside.put(f"{WS}/config/procedures/{one['id']}", json=edited,
                         headers={"Authorization": f"Bearer {ingest}"}).status_code == 403            # wrong scope
    assert agentside.post(f"{WS}/releases", headers=auth).status_code == 401                          # can't publish
