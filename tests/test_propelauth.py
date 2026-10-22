"""Signing in with PropelAuth, behind respx (no network).

PropelAuth serves standard OpenID Connect under its auth URL, so the provider is the same shape as Google's. What is
worth pinning down here is what differs: the token call authenticates with HTTP Basic, the stable id is PropelAuth's
`user_id` (which survives an email change), and the name arrives in two fields.

SOPOPS_LOGIN_PROVIDERS is here too: it exists so the hosted sopops can offer PropelAuth and nothing else.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
import respx
from conftest import SOPC_BIN, fresh_db_url
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sopops_server import Sopc, Store, create_app
from sopops_server.auth import mode, providers
from sopops_server.orm import Identity, User

AUTH_URL = "https://test-1234.propelauthtest.com"
AUTHORIZE = f"{AUTH_URL}/propelauth/oauth/authorize"
TOKEN = f"{AUTH_URL}/propelauth/oauth/token"
USERINFO = f"{AUTH_URL}/propelauth/oauth/userinfo"


@pytest.fixture
def propel_env(monkeypatch):
    monkeypatch.setenv("SOPOPS_PROPELAUTH_URL", AUTH_URL + "/")  # a trailing slash must not double up in the URLs
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_ID", "pa-client")
    monkeypatch.setenv("SOPOPS_PROPELAUTH_CLIENT_SECRET", "pa-secret")


@pytest.fixture
def app(tmp_path, propel_env):
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


class PropelAuth:
    """Their token and userinfo endpoints, and a record of how the token call authenticated."""

    def __init__(self, router: respx.MockRouter, user_id: str, email: str, verified: bool = True,
                 first: str = "Ana", last: str = "Diaz"):
        router.post(TOKEN).mock(side_effect=self.token)
        router.get(USERINFO).mock(return_value=httpx.Response(200, json={
            "user_id": user_id, "email": email, "email_verified": verified, "first_name": first, "last_name": last,
            "picture_url": "https://pictures.example/p"}))
        self.auth_header: str | None = None
        self.form: dict | None = None

    def token(self, request: httpx.Request) -> httpx.Response:
        self.auth_header = request.headers.get("authorization")
        self.form = parse_qs(request.content.decode())
        return httpx.Response(200, json={"access_token": "pa-token", "token_type": "Bearer", "expires_in": 3600})


def start(c: TestClient) -> str:
    res = c.get("/v1/auth/login/propelauth", params={"next": "/"}, follow_redirects=False)
    assert res.status_code == 302, res.text
    url = urlparse(res.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE
    q = parse_qs(url.query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    return q["state"][0]


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

    # The token call authenticates with Basic, not with the secret in the form.
    assert pa.auth_header == "Basic " + base64.b64encode(b"pa-client:pa-secret").decode()
    assert "client_secret" not in (pa.form or {})

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
