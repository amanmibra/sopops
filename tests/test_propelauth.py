"""Signing in with PropelAuth, behind respx (no network).

PropelAuth serves standard OpenID Connect under its auth URL, so the provider is the same shape as Google's. What is
worth pinning down here is what differs: the token call posts the client secret in the form rather than as Basic
auth, the verified-email flag is `email_confirmed`, the stable id is their `user_id` (which survives an email
change), and the name arrives in two fields.

Signing out differs too, and is the other half of this file. Their logout isn't OAuth2's: the refresh token goes back
to `/api/backend/v1/logout` from the backend, with no API key. So the refresh token is kept, encrypted, on the login
session, and signing out spends it before revoking. None of that may make signing out fail.

Their organizations are the fourth difference, and the third part of this file: PropelAuth owns the teams, so every
sign-in mirrors the ones its userinfo reports (`org_id_to_org_info`) into sopops's own, with the role it gave. What
matters is that it stays a mirror: renaming a team there renames one team here rather than making a second, leaving
one there leaves it here, and a team made in sopops is left alone by all of it, as is every other provider.

Making a team and inviting people to one are the fifth, and the fourth part of this file. With
SOPOPS_PROPELAUTH_API_KEY set, sopops's own screens do both through their backend API, and nobody is sent to
anybody's hosted pages: making a team posts to `org/` and then `org/add_user`, and the team that comes back is
mirrored by the same code a sign-in uses, so the next sign-in finds it rather than making a second. Invites go to
`invite_user`, the ones waiting come from `pending_org_invites` and go away through it. Without the key the
capability is simply off, and the hosted page is the fallback it always was. The last thing these check is what
happens when PropelAuth says no or says nothing: one plain sentence and a 502, never a 500, and never their text.

SOPOPS_LOGIN_PROVIDERS is here too: it exists so the hosted sopops can offer PropelAuth and nothing else.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
import pytest
import respx
from conftest import SOPC_BIN, fresh_db_url
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sopops_server import Sopc, Store, create_app
from sopops_server.auth import access, mode, providers
from sopops_server.runtime import crypto
from sopops_server.orm import Identity, Invite, LoginSession, Membership, Org, User

AUTH_URL = "https://test-1234.propelauthtest.com"
AUTHORIZE = f"{AUTH_URL}/propelauth/oauth/authorize"
TOKEN = f"{AUTH_URL}/propelauth/oauth/token"
USERINFO = f"{AUTH_URL}/propelauth/oauth/userinfo"
LOGOUT = f"{AUTH_URL}/api/backend/v1/logout"
CREATE_ORG = f"{AUTH_URL}/api/backend/v1/org/"
ADD_USER = f"{AUTH_URL}/api/backend/v1/org/add_user"
CHANGE_ROLE = f"{AUTH_URL}/api/backend/v1/org/change_role"
INVITE_USER = f"{AUTH_URL}/api/backend/v1/invite_user"
PENDING = f"{AUTH_URL}/api/backend/v1/pending_org_invites"
REFRESH = "pa-refresh-3f9c"
API_KEY = "pa-api-key-7b2"


@pytest.fixture
def propel_env(monkeypatch):
    monkeypatch.setenv("SOPOPS_PROPELAUTH_URL", AUTH_URL + "/")  # a trailing slash must not double up in the URLs
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_ID", "pa-client")
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_SECRET", "pa-secret")
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())  # the refresh token is stored encrypted


@pytest.fixture
def api_key(monkeypatch):
    """Their backend API wants one. Without it sopops makes no teams and sends no invites through them."""
    monkeypatch.setenv("SOPOPS_PROPELAUTH_API_KEY", API_KEY)


@pytest.fixture
def app(tmp_path, propel_env):
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


class PropelAuth:
    """Their token, userinfo and logout endpoints, and a record of how each call was made."""

    def __init__(self, router: respx.MockRouter, user_id: str, email: str, verified: bool = True,
                 first: str = "Ana", last: str = "Diaz", logout: object = None, orgs: list[tuple] | None = None):
        router.post(TOKEN).mock(side_effect=self.token)
        router.get(USERINFO).mock(return_value=httpx.Response(200, json={
            "user_id": user_id, "email": email, "email_confirmed": verified, "first_name": first, "last_name": last,
            "picture_url": "https://pictures.example/p", "org_id_to_org_info": org_info(orgs or [])}))
        self.logout = router.post(LOGOUT).mock(side_effect=logout or self.end)
        self.auth_header: str | None = None
        self.form: dict | None = None
        self.logout_auth: str | None = None
        self.logged_out: list[dict] = []

    def token(self, request: httpx.Request) -> httpx.Response:
        self.auth_header = request.headers.get("authorization")
        self.form = parse_qs(request.content.decode())
        return httpx.Response(200, json={"access_token": "pa-token", "refresh_token": REFRESH, "token_type": "Bearer", "expires_in": 3600})

    def end(self, request: httpx.Request) -> httpx.Response:
        self.logout_auth = request.headers.get("authorization")
        self.logged_out.append(json.loads(request.content.decode()))
        return httpx.Response(200)


def start(c: TestClient) -> str:
    res = c.get("/v1/auth/login/propelauth", params={"next": "/"}, follow_redirects=False)
    assert res.status_code == 302, res.text
    url = urlparse(res.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE
    q = parse_qs(url.query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    return q["state"][0]


def org_info(orgs: list[tuple]) -> dict:
    """Their `org_id_to_org_info`, from `(org_id, org_name, user_role)` triples. Keyed on the org id, as theirs is,
    and carrying the same fields: this is the shape a live test environment answers with."""
    return {id: {"org_id": id, "org_name": name, "url_safe_org_name": name.lower().replace(" ", "-"),
                 "user_role": role, "inherited_user_roles_plus_current_role": [role]} for id, name, role in orgs}


def sign_in(c: TestClient, router: respx.MockRouter, user_id: str = "pa_user_9", email: str = "ana@example.com",
            logout: object = None, orgs: list[tuple] | None = None) -> PropelAuth:
    """Sign in with PropelAuth and leave the client holding the session and CSRF cookies."""
    pa = PropelAuth(router, user_id, email, logout=logout, orgs=orgs)
    st = start(c)
    res = c.get("/v1/auth/callback/propelauth", params={"code": "the-code", "state": st}, follow_redirects=False)
    assert res.status_code == 303, res.text
    c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    return pa


def the_session(app) -> LoginSession:
    with Session(app.state.store.engine) as d:
        return d.exec(select(LoginSession)).one()


def test_it_is_offered_only_once_it_is_configured(monkeypatch, tmp_path):
    for k in ("SOPOPS_PROPELAUTH_URL", "SOPOPS_PROPELAUTH_CLIENT_ID", "SOPOPS_PROPELAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert providers.get("propelauth") is not None and not providers.get("propelauth").configured()
    monkeypatch.setenv("SOPOPS_PROPELAUTH_URL", AUTH_URL)
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_ID", "pa-client")
    assert not providers.get("propelauth").configured()  # still no secret
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_SECRET", "pa-secret")
    assert providers.get("propelauth").configured()


def test_signing_in_makes_a_user_keyed_on_their_user_id(app):
    c = TestClient(app)
    assert {p["id"] for p in c.get("/v1/auth/session").json()["providers"]} >= {"propelauth"}
    with respx.mock(assert_all_called=False) as router:
        pa = PropelAuth(router, "pa_user_9", "ana@example.com")
        st = start(c)
        res = c.get("/v1/auth/callback/propelauth", params={"code": "the-code", "state": st}, follow_redirects=False)
    assert res.status_code == 303, res.text

    # The client authenticates in the form (`client_secret_post`), not as Basic auth, and PKCE rides along: their
    # discovery document advertises basic and post, and their OAuth guidance says no Authorization header with PKCE.
    assert pa.auth_header is None
    assert (pa.form or {}).get("client_secret") == ["pa-secret"]
    assert (pa.form or {}).get("client_id") == ["pa-client"] and (pa.form or {}).get("code_verifier")

    with Session(app.state.store.engine) as d:
        user = d.exec(select(User)).one()
        ident = d.exec(select(Identity)).one()
    assert user.email == "ana@example.com" and user.name == "Ana Diaz"
    assert ident.provider == "propelauth" and ident.provider_user_id == "pa_user_9"


def test_an_unverified_email_is_refused(app):
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        PropelAuth(router, "pa_user_1", "eve@example.com", verified=False)
        st = start(c)
        res = c.get("/v1/auth/callback/propelauth", params={"code": "the-code", "state": st}, follow_redirects=False)
    assert "no%20verified%20email" in res.headers["location"] and "PropelAuth" in unquote(res.headers["location"])
    with Session(app.state.store.engine) as d:
        assert d.exec(select(User)).all() == []


def test_the_address_can_change_without_making_a_second_account(app):
    """PropelAuth's user_id is stable across an email change, and the identity is keyed on it."""
    c = TestClient(app)
    for email in ("ana@example.com", "ana@newplace.example"):
        with respx.mock(assert_all_called=False) as router:
            PropelAuth(router, "pa_user_9", email)
            st = start(c)
            c.get("/v1/auth/callback/propelauth", params={"code": "the-code", "state": st}, follow_redirects=False)
    with Session(app.state.store.engine) as d:
        assert len(d.exec(select(User)).all()) == 1
        assert len(d.exec(select(Identity)).all()) == 1


# --- their organizations are the teams --------------------------------------------------


def teams(app) -> dict[str, str | None]:
    """Every team here, by name, with the provider's org id it mirrors (None for one made in sopops). `Default` is
    the org a fresh install makes for its first workspace; with PropelAuth owning the teams, nobody is put in it."""
    with Session(app.state.store.engine) as d:
        return {o.name: o.external_id for o in d.exec(select(Org))}


def roles(app) -> dict[str, str]:
    """Every team the one user is in, by name, and their role in it."""
    with Session(app.state.store.engine) as d:
        return {d.get(Org, m.org_id).name: m.role for m in d.exec(select(Membership))}


def made_here(app, name: str = "Made here") -> None:
    """A team made in sopops, that PropelAuth has never heard of, with the user in it."""
    now = datetime.now(timezone.utc)
    with Session(app.state.store.engine) as d:
        user = d.exec(select(User)).one()
        d.add(Org(id=name.lower().replace(" ", "-"), name=name, slug=name.lower().replace(" ", "-"), allowed_domains=[], created_at=now))
        d.add(Membership(org_id=name.lower().replace(" ", "-"), user_id=user.id, role="member", created_at=now))
        d.commit()


def test_a_team_at_propelauth_becomes_a_team_here(app):
    """The whole point: somebody who made a team there signs in and is in it here, with no join link in sight."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_1", "Northwind", "Owner")])

    assert teams(app) == {"Default": None, "Northwind": "propelauth:pa_org_1"}  # keyed on their org id, with the provider's name on it
    assert roles(app) == {"Northwind": "owner"}
    state = c.get("/v1/auth/session").json()
    assert state["org"]["name"] == "Northwind" and state["org"]["role"] == "owner"
    assert state["org"]["workspace"] == "northwind"  # a team to work in, not a row in a table


def test_a_renamed_team_is_the_same_team(app):
    """Matching on their org id, not the name: renaming it there renames it here instead of making a second."""
    c = TestClient(app)
    for name, role in (("Northwind", "Member"), ("Northwind Traders", "Admin")):
        with respx.mock(assert_all_called=False) as router:
            sign_in(c, router, orgs=[("pa_org_1", name, role)])

    assert teams(app) == {"Default": None, "Northwind Traders": "propelauth:pa_org_1"}
    assert roles(app) == {"Northwind Traders": "admin"}  # the role follows them too
    with Session(app.state.store.engine) as d:
        assert d.exec(select(Org).where(Org.external_id.is_not(None))).one().slug == "northwind"  # the slug is the one it was made with: addresses don't move


def test_leaving_a_team_there_leaves_it_here_and_nothing_else(app):
    """Dropped from an org at PropelAuth, dropped from the mirror. A team made in sopops is none of their business,
    and a mirrored team itself outlives the membership: its workspace and files are still there."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_1", "Northwind", "Owner"), ("pa_org_2", "Contoso", "Member")])
    made_here(app)
    assert roles(app) == {"Northwind": "owner", "Contoso": "member", "Made here": "member"}

    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_1", "Northwind", "Owner")])

    assert roles(app) == {"Northwind": "owner", "Made here": "member"}
    assert teams(app) == {"Default": None, "Northwind": "propelauth:pa_org_1", "Contoso": "propelauth:pa_org_2", "Made here": None}


def test_the_last_team_can_go_too(app):
    """An empty `org_id_to_org_info` is an answer, not a silence: somebody taken out of their only org loses it."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_1", "Northwind", "Owner")])
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
    assert roles(app) == {}
    assert c.get("/v1/auth/session").json()["org"] is None


@pytest.mark.parametrize("theirs, ours", [("Owner", "owner"), ("Admin", "admin"), ("Member", "member"), ("ADMIN", "admin"),
                                          ("Billing Manager", "member"), ("", "member")])
def test_their_roles_map_onto_sopops_three(app, theirs, ours):
    """Their default hierarchy is Owner, Admin, Member, which lines up. A role sopops has never heard of is a
    `member`: the one that can do the work and change nothing about the team."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_1", "Northwind", theirs)])
    assert roles(app) == {"Northwind": ours}


def test_nobody_is_asked_for_a_join_link_when_propelauth_owns_the_teams(app, monkeypatch):
    """With PropelAuth the only way in, sopops has no join link to offer: the answer is their create-org page."""
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "propelauth")
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
    state = c.get("/v1/auth/session").json()
    assert state["org"] is None and roles(app) == {}  # no team, and no default org claimed on their behalf
    p = state["providers"][0]
    back = base64.b64encode(b"http://testserver/v1/auth/login/propelauth").decode()
    assert p["owns_teams"] and p["new_team_url"] == f"{AUTH_URL}/create_org?rt={quote(back)}"


def test_a_github_sign_in_makes_no_teams_and_keeps_the_old_rule(app, github_env):
    """Everything above is driven by what the provider reports, and GitHub reports no teams: the first person in
    owns the org that's already there, nothing carries an external id, and the join link still has a job."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://github.com/login/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "gh-token", "token_type": "bearer"}))
        router.get("https://api.github.com/user").mock(return_value=httpx.Response(200, json={"id": 4242, "login": "ana", "name": "Ana Diaz"}))
        router.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(200, json=[{"email": "ana@example.com", "primary": True, "verified": True}]))
        st = parse_qs(urlparse(c.get("/v1/auth/login/github", follow_redirects=False).headers["location"]).query)["state"][0]
        assert c.get("/v1/auth/callback/github", params={"code": "the-code", "state": st}, follow_redirects=False).status_code == 303

    assert teams(app) == {"Default": None} and roles(app) == {"Default": "owner"}
    gh = next(p for p in c.get("/v1/auth/session").json()["providers"] if p["id"] == "github")
    assert gh["owns_teams"] is False and gh["new_team_url"] is None


# --- SOPOPS_LOGIN_PROVIDERS: which providers this deployment offers ----------------------


@pytest.fixture
def github_env(monkeypatch):
    monkeypatch.setenv("SOPOPS_GITHUB_OAUTH_CLIENT_ID", "gh-client")
    monkeypatch.setenv("SOPOPS_GITHUB_OAUTH_CLIENT_SECRET", "gh-secret")


def offered(c: TestClient) -> list[str]:
    return [p["id"] for p in c.get("/v1/auth/session").json()["providers"]]


def test_the_allowlist_offers_what_it_names_and_nothing_else(app, github_env, monkeypatch):
    c = TestClient(app)
    assert set(offered(c)) >= {"propelauth", "github", "password"}
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "propelauth")
    assert offered(c) == ["propelauth"]  # passwords go too: the hosted sopops offers PropelAuth alone
    assert mode.required() and [p.id for p in providers.registered()] == ["propelauth"]
    assert providers.get("github") is None and providers.get("propelauth") is not None


def test_a_provider_left_out_is_closed_at_the_route_too(app, github_env, monkeypatch):
    c = TestClient(app)
    assert c.get("/v1/auth/login/github", follow_redirects=False).status_code == 302
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", " PropelAuth , ")  # spaces and capitals don't matter
    assert c.get("/v1/auth/login/github", follow_redirects=False).status_code == 404
    assert c.get("/v1/auth/login/propelauth", follow_redirects=False).status_code == 302


def test_a_list_naming_nothing_configured_is_ignored_with_a_warning(app, github_env, monkeypatch, caplog):
    """A typo must not leave a deployment with no way in, so the list falls back to today's behaviour."""
    c = TestClient(app)
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "propelauht,saml")
    with caplog.at_level(logging.WARNING, logger="sopops.auth"):
        assert set(offered(c)) >= {"propelauth", "github", "password"}
    assert "SOPOPS_LOGIN_PROVIDERS" in caplog.text and "propelauht,saml" in caplog.text
    assert providers.get("github") is not None

    # One name that is configured is enough for the list to count, and the unknown name is simply skipped.
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "saml,github")
    assert offered(c) == ["github"]


def test_an_id_that_is_registered_but_not_configured_is_skipped_as_it_always_was(app, github_env, monkeypatch):
    monkeypatch.delenv("SOPOPS_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "github,google")
    assert offered(TestClient(app)) == ["github"]


def test_the_newer_email_verified_flag_counts_too(app):
    """Their userinfo says `email_confirmed`; `email_verified` arrived in April 2026. Accept either."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "pa-token", "token_type": "Bearer"}))
        router.get(USERINFO).mock(return_value=httpx.Response(200, json={
            "user_id": "pa_user_3", "email": "mo@example.com", "email_verified": True, "first_name": "Mo"}))
        st = start(c)
        res = c.get("/v1/auth/callback/propelauth", params={"code": "the-code", "state": st}, follow_redirects=False)
    assert res.status_code == 303, res.headers.get("location")
    with Session(app.state.store.engine) as d:
        assert d.exec(select(User)).one().email == "mo@example.com"


# --- signing out: ending PropelAuth's session too ---------------------------------------


def test_the_refresh_token_is_stored_encrypted_and_not_as_plaintext(app):
    """It's a credential, so it's kept the way every other stored secret is: Fernet, under SOPOPS_SECRET_KEY."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router)
    s = the_session(app)
    assert s.refresh_ciphertext and s.refresh_ciphertext != REFRESH
    assert REFRESH not in s.refresh_ciphertext
    assert crypto.decrypt(s.refresh_ciphertext) == REFRESH
    assert "refresh_ciphertext" not in s.model_dump()  # excluded, so it can't ride out in an API response


def test_signing_out_ends_the_propelauth_session_then_revokes(app):
    """Without this, the next click on "Sign in" comes straight back signed in: their session outlived ours."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        pa = sign_in(c, router)
        assert c.post("/v1/auth/logout").status_code == 204

    assert pa.logout.called and str(pa.logout.calls[0].request.url) == LOGOUT
    assert pa.logged_out == [{"refresh_token": REFRESH}]
    assert pa.logout_auth is None  # their logout takes the refresh token, not a backend API key
    assert pa.logout.calls[0].request.headers["content-type"] == "application/json"

    s = the_session(app)
    assert s.revoked_at is not None
    assert s.refresh_ciphertext is None  # spent, so it isn't kept


@pytest.mark.parametrize("answer, why", [
    (lambda request: httpx.Response(500, json={"error": "boom"}), "a 500"),
    (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("too slow", request=request)), "a timeout"),
])
def test_signing_out_still_works_when_propelauth_does_not(app, caplog, answer, why):
    """PropelAuth being down must never leave someone unable to sign out of sopops: log it and revoke anyway."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        pa = sign_in(c, router, logout=answer)
        with caplog.at_level(logging.WARNING, logger="sopops.auth"):
            assert c.post("/v1/auth/logout").status_code == 204, why
    assert pa.logout.called
    assert "PropelAuth" in caplog.text and REFRESH not in caplog.text
    assert the_session(app).revoked_at is not None
    assert c.get("/v1/auth/session").json()["user"] is None


def test_a_github_session_signs_out_without_calling_propelauth_at_all(app, github_env):
    """Only PropelAuth keeps a refresh token, so nothing is posted anywhere for anyone else."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        logout = router.post(LOGOUT).mock(return_value=httpx.Response(200))
        router.post("https://github.com/login/oauth/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "gh-token", "token_type": "bearer"}))
        router.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"id": 4242, "login": "ana", "name": "Ana Diaz"}))
        router.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(200, json=[{"email": "ana@example.com", "primary": True, "verified": True}]))

        st = parse_qs(urlparse(c.get("/v1/auth/login/github", follow_redirects=False).headers["location"]).query)["state"][0]
        assert c.get("/v1/auth/callback/github", params={"code": "the-code", "state": st}, follow_redirects=False).status_code == 303
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)

        assert the_session(app).refresh_ciphertext is None
        assert c.post("/v1/auth/logout").status_code == 204

    assert not logout.called
    assert the_session(app).revoked_at is not None


# --- making a team, and inviting people to it, from sopops's own screens ------------------


def sent(route) -> dict:
    """What was posted to their API on the first call of this route."""
    return json.loads(route.calls[0].request.content.decode())


def test_the_api_key_is_what_turns_managing_teams_on(app, monkeypatch):
    """Everything below hangs off this: no key, no team-making, and the hosted page stays the way out."""
    p = providers.get("propelauth")
    monkeypatch.delenv("SOPOPS_PROPELAUTH_API_KEY", raising=False)
    assert p.owns_teams and not p.manages_teams()
    monkeypatch.setenv("SOPOPS_PROPELAUTH_API_KEY", API_KEY)
    assert p.manages_teams()
    monkeypatch.setenv("SOPOPS_PROPELAUTH_API_KEY", "   ")  # set to nothing is not set
    assert not p.manages_teams()


def test_naming_a_team_makes_it_there_and_mirrors_it_here(app, api_key):
    """The whole of the "Name your team" screen: one POST to make it, one to make the person its owner (making an
    org through their API leaves it empty), and then the team is here, with its workspace, as if they'd signed in
    holding it."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        made = router.post(CREATE_ORG).mock(return_value=httpx.Response(200, json={"org_id": "pa_org_7", "name": "Northwind"}))
        joined = router.post(ADD_USER).mock(return_value=httpx.Response(200, json={}))
        res = c.post("/v1/orgs", json={"name": "Northwind"})

    assert res.status_code == 201, res.text
    assert sent(made) == {"name": "Northwind"}
    assert made.calls[0].request.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent(joined) == {"user_id": "pa_user_9", "org_id": "pa_org_7", "role": "Owner"}  # their role name, not sopops's

    assert teams(app) == {"Default": None, "Northwind": "propelauth:pa_org_7"}  # keyed on their id, like a mirrored one
    assert roles(app) == {"Northwind": "owner"}
    assert res.json()["workspace"] == "northwind" and res.json()["org"]["name"] == "Northwind"
    assert c.get("/v1/auth/session").json()["org"]["name"] == "Northwind"  # and they're working in it


def test_the_next_sign_in_finds_that_team_rather_than_making_a_second(app, api_key):
    """It was mirrored through the same code a sign-in uses, so the sign-in that follows agrees with it."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        router.post(CREATE_ORG).mock(return_value=httpx.Response(200, json={"org_id": "pa_org_7", "name": "Northwind"}))
        router.post(ADD_USER).mock(return_value=httpx.Response(200, json={}))
        assert c.post("/v1/orgs", json={"name": "Northwind"}).status_code == 201
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[("pa_org_7", "Northwind", "Owner")])

    assert teams(app) == {"Default": None, "Northwind": "propelauth:pa_org_7"}
    assert roles(app) == {"Northwind": "owner"}


def test_a_name_they_would_bounce_is_refused_before_anybody_is_called(app, api_key):
    """They take letters, numbers, spaces and underscores in an org name. A name with anything else costs a
    sentence, not a round trip and a refusal written for somebody else's support team."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        made = router.post(CREATE_ORG).mock(return_value=httpx.Response(200, json={"org_id": "pa_org_7", "name": "x"}))
        res = c.post("/v1/orgs", json={"name": "Northwind & Co."})
    assert res.status_code == 502 and "letters, numbers, spaces" in res.json()["detail"]
    assert not made.called


def test_with_no_api_key_the_team_is_sopops_own_and_the_hosted_page_is_offered(app, monkeypatch):
    """The fallback, which is the self-hosted story and the misconfigured one: nothing is posted anywhere, the team
    is sopops's own, and /v1/auth/session hands the UI their create-org page to link to instead of a form."""
    monkeypatch.delenv("SOPOPS_PROPELAUTH_API_KEY", raising=False)
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "propelauth")
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        made = router.post(CREATE_ORG).mock(return_value=httpx.Response(200, json={"org_id": "pa_org_7", "name": "Northwind"}))
        res = c.post("/v1/orgs", json={"name": "Northwind"})

    assert res.status_code == 201 and not made.called
    assert teams(app) == {"Default": None, "Northwind": None}  # made here, and theirs to know nothing about
    back = base64.b64encode(b"http://testserver/v1/auth/login/propelauth").decode()
    assert c.get("/v1/auth/session").json()["providers"][0]["new_team_url"] == f"{AUTH_URL}/create_org?rt={quote(back)}"


def test_the_hosted_page_is_not_offered_once_sopops_can_make_the_team(app, api_key, monkeypatch):
    """The UI reads it the other way round: no URL means "ask them here", so it must go when there's a form."""
    monkeypatch.setenv("SOPOPS_LOGIN_PROVIDERS", "propelauth")
    c = TestClient(app)
    p = c.get("/v1/auth/session").json()["providers"][0]
    assert p["owns_teams"] and p["new_team_url"] is None


def in_a_team(c: TestClient, router: respx.MockRouter, role: str = "Owner") -> None:
    """Signed in, owning a team that mirrors `pa_org_1`."""
    sign_in(c, router, orgs=[("pa_org_1", "Northwind", role)])


def test_inviting_somebody_goes_to_them_and_not_into_sopops_invites(app, api_key):
    """Their email, their invite, their team: sopops keeps nothing, so there's no second link to go stale."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router)
        invited = router.post(INVITE_USER).mock(return_value=httpx.Response(200, json={}))
        res = c.post("/v1/org/invites", json={"email": "Bo@Example.com", "role": "admin"})

    assert res.status_code == 201, res.text
    assert sent(invited) == {"email": "bo@example.com", "org_id": "pa_org_1", "role": "Admin"}
    assert invited.calls[0].request.headers["authorization"] == f"Bearer {API_KEY}"
    assert res.json()["email"] == "bo@example.com" and res.json()["emailed"] is True
    assert res.json()["link"] == ""  # there's no sopops link for an invite sopops doesn't hold
    with Session(app.state.store.engine) as d:
        assert d.exec(select(Invite)).all() == []


def test_only_an_owner_invites_an_owner_even_through_them(app, api_key):
    """sopops's own rule about handing out roles is checked before anybody is called."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router, role="Admin")
        invited = router.post(INVITE_USER).mock(return_value=httpx.Response(200, json={}))
        res = c.post("/v1/org/invites", json={"email": "bo@example.com", "role": "owner"})
    assert res.status_code == 403 and not invited.called


def test_the_invites_waiting_are_theirs_and_are_withdrawn_by_address(app, api_key):
    """Their pending invites have no id of their own, so the address is the id sopops shows and withdraws by."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router)
        listed = router.get(PENDING).mock(return_value=httpx.Response(200, json={
            "invites": [{"invitee_email": "bo@example.com", "org_id": "pa_org_1", "org_name": "Northwind", "role_in_org": "Admin",
                         "created_at": 1718648493, "expires_at": 1719080493, "inviter_email": "ana@example.com"}],
            "total_invites": 1, "page_size": 100, "current_page": 0, "has_more_results": False}))
        got = c.get("/v1/org/invites")
        dropped = router.delete(PENDING).mock(return_value=httpx.Response(200, json={}))
        gone = c.delete("/v1/org/invites/bo@example.com")

    assert got.status_code == 200, got.text
    assert [(i["id"], i["email"], i["role"]) for i in got.json()] == [("bo@example.com", "bo@example.com", "admin")]
    assert got.json()[0]["created_at"].startswith("2024-06-17")  # their seconds since the epoch, as a time
    assert dict(parse_qs(listed.calls[0].request.url.query.decode())) == {"org_id": ["pa_org_1"], "page_size": ["100"], "page_number": ["0"]}
    assert gone.status_code == 204, gone.text
    assert sent(dropped) == {"org_id": "pa_org_1", "invitee_email": "bo@example.com"}


def second_member(app, role: str = "member") -> None:
    """Somebody else in the mirrored team, who has signed in with PropelAuth, so their id there is known."""
    now = datetime.now(timezone.utc)
    with Session(app.state.store.engine) as d:
        org = d.exec(select(Org).where(Org.external_id == "propelauth:pa_org_1")).one()
        d.add(User(id="u2", email="bo@example.com", name="Bo", created_at=now))
        d.add(Identity(id="i2", user_id="u2", provider="propelauth", provider_user_id="pa_user_2", email="bo@example.com", created_at=now))
        d.add(Membership(org_id=org.id, user_id="u2", role=role, created_at=now))
        d.commit()


def test_a_role_change_is_made_there_too_so_the_next_sign_in_keeps_it(app, api_key):
    """Their roles are mirrored over sopops's at every sign-in, so a role changed only here wouldn't last the day."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router)
        second_member(app)
        changed = router.post(CHANGE_ROLE).mock(return_value=httpx.Response(200, json={}))
        res = c.patch("/v1/org/members/u2", json={"role": "admin"})
    assert res.status_code == 200, res.text
    assert sent(changed) == {"user_id": "pa_user_2", "org_id": "pa_org_1", "role": "Admin"}
    assert res.json()["role"] == "admin"


# --- when they say no, or say nothing -----------------------------------------------------


@pytest.mark.parametrize("answer, why", [
    (lambda request: httpx.Response(500, json={"error": "boom"}), "a 500"),
    (lambda request: httpx.Response(400, json={"field_errors": {"name": "something about organizations"}}), "a refusal"),
    (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("too slow", request=request)), "a timeout"),
])
def test_a_team_that_cannot_be_made_is_a_sentence_and_not_a_500(app, api_key, caplog, answer, why):
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        router.post(CREATE_ORG).mock(side_effect=answer)
        with caplog.at_level(logging.WARNING, logger="sopops.auth"):
            res = c.post("/v1/orgs", json={"name": "Northwind"})

    assert res.status_code == 502, (why, res.text)
    assert res.json()["detail"] == "Making the team didn't work just now. Try again in a moment."
    assert "PropelAuth" not in res.text and "organizations" not in res.text  # their name and their words stay in the log
    assert "PropelAuth" in caplog.text
    assert teams(app) == {"Default": None}  # nothing half-made here
    assert c.get("/v1/auth/session").status_code == 200  # and the app still answers


def test_an_invite_they_refuse_says_so_plainly(app, api_key, caplog):
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router)
        router.post(INVITE_USER).mock(return_value=httpx.Response(503, text="upstream down"))
        with caplog.at_level(logging.WARNING, logger="sopops.auth"):
            res = c.post("/v1/org/invites", json={"email": "bo@example.com", "role": "member"})
    assert res.status_code == 502
    assert res.json()["detail"] == "Sending the invite didn't work just now. Try again in a moment."
    assert "upstream down" not in res.text and "PropelAuth" in caplog.text


def test_the_members_page_still_opens_when_their_invites_cannot_be_read(app, api_key):
    """A list that won't load must not take the page with it: the people are sopops's own and still show."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        in_a_team(c, router)
        router.get(PENDING).mock(return_value=httpx.Response(500, json={}))
        invites = c.get("/v1/org/invites")
        members = c.get("/v1/org/members")
    assert invites.status_code == 502 and invites.json()["detail"] == "Loading the invites didn't work just now. Try again in a moment."
    assert members.status_code == 200 and [m["email"] for m in members.json()] == ["ana@example.com"]


def test_an_empty_org_is_logged_loudly_when_they_will_not_add_its_owner(app, api_key, caplog):
    """The second call failing leaves an org there with nobody in it. Nobody sees it, but somebody should know."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        router.post(CREATE_ORG).mock(return_value=httpx.Response(200, json={"org_id": "pa_org_7", "name": "Northwind"}))
        router.post(ADD_USER).mock(return_value=httpx.Response(403, json={}))
        with caplog.at_level(logging.WARNING, logger="sopops.auth"):
            res = c.post("/v1/orgs", json={"name": "Northwind"})
    assert res.status_code == 502
    assert "pa_org_7" in caplog.text and "empty" in caplog.text
    assert teams(app) == {"Default": None}


def test_a_team_made_in_sopops_keeps_sopops_invites_even_with_the_key_set(app, api_key):
    """The branch is the team's, not the deployment's: a team with no id of theirs is sopops's, invites and all."""
    c = TestClient(app)
    with respx.mock(assert_all_called=False) as router:
        sign_in(c, router, orgs=[])
        made_here(app, "Made here")
        with Session(app.state.store.engine) as d:  # the one membership it made, as its owner, so invites are allowed
            m = d.exec(select(Membership).where(Membership.org_id == "made-here")).one()
            m.role = "owner"
            d.add(m)
            d.commit()
        invited = router.post(INVITE_USER).mock(return_value=httpx.Response(200, json={}))
        res = c.post("/v1/org/invites", json={"email": "bo@example.com", "role": "member"})

    assert res.status_code == 201, res.text
    assert not invited.called and res.json()["link"].startswith("http")
    with Session(app.state.store.engine) as d:
        assert [i.email for i in d.exec(select(Invite))] == ["bo@example.com"]
