"""Signing in with PropelAuth, behind respx (no network).

PropelAuth serves standard OpenID Connect under its auth URL, so the provider is the same shape as Google's. What is
worth pinning down here is what differs: the token call posts the client secret in the form rather than as Basic
auth, the verified-email flag is `email_confirmed`, the stable id is their `user_id` (which survives an email
change), and the name arrives in two fields.

Signing out differs too, and is the other half of this file. Their logout isn't OAuth2's: the refresh token goes back
to `/api/backend/v1/logout` from the backend, with no API key. So the refresh token is kept, encrypted, on the login
session, and signing out spends it before revoking. None of that may make signing out fail.

SOPOPS_LOGIN_PROVIDERS is here too: it exists so the hosted sopops can offer PropelAuth and nothing else.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, unquote, urlparse

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
from sopops_server.orm import Identity, LoginSession, User

AUTH_URL = "https://test-1234.propelauthtest.com"
AUTHORIZE = f"{AUTH_URL}/propelauth/oauth/authorize"
TOKEN = f"{AUTH_URL}/propelauth/oauth/token"
USERINFO = f"{AUTH_URL}/propelauth/oauth/userinfo"
LOGOUT = f"{AUTH_URL}/api/backend/v1/logout"
REFRESH = "pa-refresh-3f9c"


@pytest.fixture
def propel_env(monkeypatch):
    monkeypatch.setenv("SOPOPS_PROPELAUTH_URL", AUTH_URL + "/")  # a trailing slash must not double up in the URLs
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_ID", "pa-client")
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_SECRET", "pa-secret")
    monkeypatch.setenv("SOPOPS_SECRET_KEY", Fernet.generate_key().decode())  # the refresh token is stored encrypted


@pytest.fixture
def app(tmp_path, propel_env):
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


class PropelAuth:
    """Their token, userinfo and logout endpoints, and a record of how each call was made."""

    def __init__(self, router: respx.MockRouter, user_id: str, email: str, verified: bool = True,
                 first: str = "Ana", last: str = "Diaz", logout: object = None):
        router.post(TOKEN).mock(side_effect=self.token)
        router.get(USERINFO).mock(return_value=httpx.Response(200, json={
            "user_id": user_id, "email": email, "email_confirmed": verified, "first_name": first, "last_name": last,
            "picture_url": "https://pictures.example/p"}))
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


def sign_in(c: TestClient, router: respx.MockRouter, user_id: str = "pa_user_9", email: str = "ana@example.com",
            logout: object = None) -> PropelAuth:
    """Sign in with PropelAuth and leave the client holding the session and CSRF cookies."""
    pa = PropelAuth(router, user_id, email, logout=logout)
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
