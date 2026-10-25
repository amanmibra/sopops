"""sopops under a path: a UI built for `/app/` with its API at `/api`, behind a router that strips the prefix and says
which in X-Forwarded-Prefix (sopops.dev), or reached with the prefix still on. The default, everything at the root,
is what every other test runs."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from conftest import SOPC_BIN, fresh_db_url
from fastapi.testclient import TestClient
from test_auth import GitHub, providers_env  # noqa: F401  (a fixture)

from sopops_server import Sopc, Store, create_app
from sopops_server.urls import Layout, clean_prefix, with_prefix

INDEX = """<!doctype html><html><head>
<meta name="sopops-api-base" content="/api">
<script type="module" crossorigin src="/app/assets/index-abc.js"></script>
</head><body><div id="root"></div></body></html>"""


def built(root: Path, html: str = INDEX) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(html)
    (root / "assets" / "index-abc.js").write_text("console.log('sopops')")
    return root


@pytest.fixture
def app(tmp_path, monkeypatch, providers_env):  # noqa: F811
    monkeypatch.setenv("SOPOPS_WEB_DIR", str(built(tmp_path / "web")))
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


def edge(prefix: str) -> dict:
    """What the sopops.dev router adds to a request it forwards with `prefix` taken off."""
    return {"X-Forwarded-Prefix": prefix, "X-Forwarded-Host": "sopops.dev", "X-Forwarded-Proto": "https"}


def test_the_layout_comes_from_the_built_ui(tmp_path):
    assert Layout.of(built(tmp_path / "a")) == Layout(ui="/app", api="/api")
    # Built for a sub-path with no API base of its own: the API is under the UI's base, as the build assumes.
    alone = built(tmp_path / "b", '<script type="module" crossorigin src="/sopops/assets/index-abc.js"></script>')
    assert Layout.of(alone) == Layout(ui="/sopops", api="/sopops")
    assert Layout.of(built(tmp_path / "c", '<script type="module" crossorigin src="/assets/index-abc.js"></script>')) == Layout()
    assert Layout.of(None) == Layout() and Layout.of(tmp_path / "missing") == Layout()


def test_only_a_plain_path_is_a_prefix():
    assert [clean_prefix(v) for v in ("/app", "/app/", "/a/b", "/api, /x")] == ["/app", "/app", "/a/b", "/api"]
    assert [clean_prefix(v) for v in ("", "/", "app", "//evil.example", "/a//b", "/a?b", "https://x", None)] == [""] * 8
    assert with_prefix("/app", "/") == "/app/" and with_prefix("/app", "/w/demo?x=1") == "/app/w/demo?x=1"
    assert with_prefix("/app", "/app/w/demo") == "/app/w/demo" and with_prefix("", "/w/demo") == "/w/demo"
    assert with_prefix("/app", "//evil.example") == "//evil.example"


def test_the_ui_is_served_under_its_base_stripped_or_not(app):
    c = TestClient(app)
    # Through the router: the prefix is off and the header says what it was.
    page = c.get("/w/demo", headers=edge("/app"))
    assert page.status_code == 200 and 'src="/app/assets/index-abc.js"' in page.text
    asset = c.get("/assets/index-abc.js", headers=edge("/app"))
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    # Straight to the server, prefix on.
    assert c.get("/app/w/demo").status_code == 200 and c.get("/app/assets/index-abc.js").text == "console.log('sopops')"
    assert c.get("/app").status_code == 200


def test_a_page_outside_the_base_moves_under_it(app):
    c = TestClient(app)
    res = c.get("/w/demo/sessions?agent=a", follow_redirects=False)
    assert res.status_code == 302 and res.headers["location"] == "/app/w/demo/sessions?agent=a"
    assert c.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_the_api_answers_under_its_prefix_and_only_the_api(app):
    c = TestClient(app)
    assert c.get("/v1/auth/session", headers=edge("/api")).json()["required"] is True
    assert c.get("/api/v1/auth/session").json()["required"] is True
    assert c.get("/healthz", headers=edge("/api")).json() == {"ok": True}
    assert c.get("/api/healthz").json() == {"ok": True}
    assert c.get("/v1/auth/session").status_code == 200  # at the root too: the existing address keeps working
    # No route under the API's prefix is the API's 404, not the UI, and not a redirect into it.
    for res in (c.get("/nothing", headers=edge("/api"), follow_redirects=False), c.get("/api/nothing", follow_redirects=False)):
        assert res.status_code == 404 and res.json() == {"detail": "Not Found"}
    # FastAPI's docs ask for the spec where the browser can reach it.
    assert "/api/openapi.json" in c.get("/docs", headers=edge("/api")).text


def test_sign_in_goes_through_the_public_addresses(app, monkeypatch):
    monkeypatch.setenv("SOPOPS_PUBLIC_URL", "https://sopops.dev/app")
    monkeypatch.setenv("SOPOPS_PUBLIC_API_URL", "https://sopops.dev/api")
    c = TestClient(app, base_url="https://testserver")
    with respx.mock(assert_all_called=False) as router:
        GitHub(router, "42", "ana@example.com")
        res = c.get("/v1/auth/login/github", params={"next": "/w/demo/sessions"}, headers=edge("/api"), follow_redirects=False)
        assert res.status_code == 302
        assert parse_qs(urlparse(res.headers["location"]).query)["redirect_uri"] == ["https://sopops.dev/api/v1/auth/callback/github"]
        flow = next(h for h in res.headers.get_list("set-cookie") if h.startswith("sopops_oauth="))
        assert "Path=/api/v1/auth/callback" in flow
        st = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
        # The browser sends the flow's cookie to /api/v1/auth/callback, which the router forwards as /v1/auth/callback.
        c.cookies.clear()
        cookie = {"Cookie": flow.split(";", 1)[0], **edge("/api")}
        back = c.get("/v1/auth/callback/github", params={"code": "the-code", "state": st}, headers=cookie, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/app/w/demo/sessions"
    session = next(h for h in back.headers.get_list("set-cookie") if h.startswith("sopops_session="))
    assert "Path=/;" in session or session.endswith("Path=/")  # the UI at /app and the API at /api both get it
    cleared = next(h for h in back.headers.get_list("set-cookie") if h.startswith("sopops_oauth="))
    assert "Path=/api/v1/auth/callback" in cleared
    # A failed callback lands on the UI's sign-in page.
    failed = c.get("/v1/auth/callback/github", params={"error": "access_denied"}, headers=edge("/api"), follow_redirects=False)
    assert failed.status_code == 303 and failed.headers["location"].startswith("/app/sign-in?error=")


def test_without_public_urls_the_request_and_the_layout_decide(app):
    c = TestClient(app, base_url="https://sopops.dev")
    res = c.get("/v1/auth/login/github", headers=edge("/api"), follow_redirects=False)
    assert parse_qs(urlparse(res.headers["location"]).query)["redirect_uri"] == ["https://sopops.dev/api/v1/auth/callback/github"]
    fail = c.get("/v1/auth/callback/github", params={"error": "access_denied"}, headers=edge("/api"), follow_redirects=False)
    assert fail.headers["location"].startswith("/app/sign-in?error=")


def test_only_the_public_url_keeps_the_api_under_it(app, monkeypatch):
    """SOPOPS_PUBLIC_URL alone, as every deployment before SOPOPS_PUBLIC_API_URL: callbacks stay where they were."""
    monkeypatch.setenv("SOPOPS_PUBLIC_URL", "https://app.sopops.dev")
    c = TestClient(app, base_url="https://app.sopops.dev")
    res = c.get("/api/v1/auth/login/github", follow_redirects=False)
    assert parse_qs(urlparse(res.headers["location"]).query)["redirect_uri"] == ["https://app.sopops.dev/v1/auth/callback/github"]
    assert "Path=/v1/auth/callback" in next(h for h in res.headers.get_list("set-cookie") if h.startswith("sopops_oauth="))
    # The UI's pages are at the root of that address, so the server sends the browser on under the build's base.
    fail = c.get("/v1/auth/callback/github", params={"error": "access_denied"}, follow_redirects=False)
    assert fail.headers["location"].startswith("/sign-in?error=")
    assert c.get(fail.headers["location"], follow_redirects=False).headers["location"].startswith("/app/sign-in?error=")


def test_a_ui_at_the_root_stays_out_of_a_prefixed_api(tmp_path, monkeypatch):
    """The UI built for the root (the default) and served from elsewhere: the API behind /api answers 404 for what isn't
    a route, rather than this server's copy of the UI."""
    monkeypatch.setenv("SOPOPS_WEB_DIR", str(built(tmp_path / "web", '<script type="module" crossorigin src="/assets/index-abc.js"></script>')))
    c = TestClient(create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))))
    assert c.get("/healthz", headers=edge("/api")).json() == {"ok": True}
    res = c.get("/w/demo", headers=edge("/api"), follow_redirects=False)
    assert res.status_code == 404 and res.json() == {"detail": "Not Found"}
    assert c.get("/w/demo").status_code == 200  # straight to the server, the UI as always
