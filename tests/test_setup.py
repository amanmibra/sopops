"""First-run setup and teams: naming the team and making its one workspace, adding people from setup, join links, the
platforms a team uses, which workspace a team works in, switching and making teams, and the migration leaving existing
orgs as they were."""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import SOPC_BIN, downgrade, fresh_db_url
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from sopops_server import Sopc, Store, create_app
from sopops_server.auth import access
from sopops_server.db import upgrade
from sopops_server.orm import FileVersion, JoinLink, LoginAttempt, Membership, Org, User, Workspace

PW = "correct horse battery"
ALL = ["livekit", "elevenlabs", "vapi"]


def with_csrf(c: TestClient, res: httpx.Response) -> httpx.Response:
    if c.cookies.get(access.CSRF_COOKIE):
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.CSRF_COOKIE)
    return res


def db(c: TestClient) -> Session:
    return Session(c.app.state.store.engine)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A fresh install with passwords and nothing else: no users, no workspaces."""
    monkeypatch.setenv("SOPOPS_AUTH_REQUIRED", "true")
    return create_app(Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN)))


def first_run(app, team: str = "Northwind", email: str = "ana@example.com") -> TestClient:
    """Steps 1 to 3: name, email and password, then the team."""
    c = TestClient(app)
    assert c.get("/v1/auth/session").json()["first_account"]
    res = with_csrf(c, c.post("/v1/auth/password/first-account", json={"email": email, "name": "Ana", "password": PW}))
    assert res.status_code == 200, res.text
    res = c.post("/v1/org/setup", json={"team": team})
    assert res.status_code == 200, res.text
    return c


def join(app, link: str, email: str, name: str = "") -> TestClient:
    c = TestClient(app)
    res = with_csrf(c, c.post("/v1/auth/join", json={"token": link.split("#")[1], "email": email, "name": name, "password": PW}))
    assert res.status_code == 200, res.text
    return c


# --- the first run ----------------------------------------------------------------------------


def test_the_first_run_names_the_team_and_makes_its_workspace(fresh):
    c = first_run(fresh)
    me = c.get("/v1/auth/session").json()
    assert me["user"]["name"] == "Ana" and me["user"]["email"] == "ana@example.com"
    assert me["org"]["name"] == "Northwind" and me["org"]["slug"] == "northwind" and me["org"]["role"] == "owner"
    assert me["org"]["workspace"] == "northwind"
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["northwind"]
    assert c.get("/v1/workspaces/northwind/config").status_code == 200  # empty, but there: no "create a workspace" screen
    with db(c) as d:
        assert [(o.name, o.slug) for o in d.exec(select(Org))] == [("Northwind", "northwind")]  # the install's one org, renamed
        assert d.exec(select(Workspace)).one().org_id == me["org"]["id"]
    # setting up again renames, and keeps the one workspace
    again = c.post("/v1/org/setup", json={"team": "Northwind Labs"}).json()
    assert again["workspace"] == "northwind" and again["org"]["name"] == "Northwind Labs" and again["org"]["slug"] == "northwind-labs"
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["northwind"]
    assert c.post("/v1/org/setup", json={"team": "  "}).status_code in (400, 422)


def test_setup_keeps_a_workspace_the_org_has(pw_app_with_demo):
    c = first_run(pw_app_with_demo, team="Tony's Pizza")
    assert c.get("/v1/auth/session").json()["org"]["workspace"] == "demo"
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["demo"]


@pytest.fixture
def pw_app_with_demo(fresh, files):
    fresh.state.store.replace("demo", files)
    return fresh


def test_only_owners_and_admins_set_up(fresh):
    c = first_run(fresh)
    link = c.post("/v1/org/join-link", json={"role": "member"}).json()["link"]
    bo = join(fresh, link, "bo@example.com")
    assert bo.post("/v1/org/setup", json={"team": "Mine"}).status_code == 403
    assert TestClient(fresh).post("/v1/org/setup", json={"team": "Mine"}).status_code == 401


def test_adding_people_from_setup(fresh):
    c = first_run(fresh)
    added = c.post("/v1/org/members", json={"emails": ["bo@example.com", "cy@example.com"], "role": "admin"})
    assert added.status_code == 200, added.text
    got = added.json()
    assert [p["status"] for p in got] == ["invited", "invited"]
    assert all(p["invite"]["link"].startswith("http://testserver/invite#") and not p["invite"]["emailed"] for p in got)  # links to send on
    assert {i["email"]: i["role"] for i in c.get("/v1/org/invites").json()} == {"bo@example.com": "admin", "cy@example.com": "admin"}
    assert c.get("/v1/org").json()["name"] == "Northwind"


# --- join links -------------------------------------------------------------------------------


def test_a_join_link_makes_an_account_in_the_team(fresh):
    c = first_run(fresh)
    assert c.get("/v1/org/join-link").json() is None
    made = c.post("/v1/org/join-link", json={})
    assert made.status_code == 201, made.text
    link = made.json()
    assert link["role"] == "member" and link["joined"] == 0 and link["link"].startswith("http://testserver/join#")
    days = (datetime.fromisoformat(link["expires_at"]) - datetime.fromisoformat(link["created_at"])).days
    assert days == 7
    shown = c.get("/v1/org/join-link").json()
    assert shown["id"] == link["id"] and "link" not in shown  # the address is shown once
    token = link["link"].split("#")[1]
    with db(c) as d:
        assert d.exec(select(JoinLink)).one().token_hash != token  # stored hashed

    anon = TestClient(fresh)
    info = anon.post("/v1/auth/join/lookup", json={"token": token}).json()
    assert info["org_name"] == "Northwind" and info["role"] == "member" and not info["member"] and info["needs_platforms"]
    bo = join(fresh, link["link"], "Bo@Example.com", "Bo")
    me = bo.get("/v1/auth/session").json()
    assert me["user"]["email"] == "bo@example.com" and me["org"]["name"] == "Northwind" and me["org"]["role"] == "member"
    assert me["org"]["workspace"] == "northwind"
    assert [w["workspace"] for w in bo.get("/v1/workspaces").json()] == ["northwind"]
    # anyone can use it, many times
    join(fresh, link["link"], "cy@example.com")
    assert c.get("/v1/org/join-link").json()["joined"] == 2
    # an address with an account signs in, then joins
    res = TestClient(fresh).post("/v1/auth/join", json={"token": token, "email": "bo@example.com", "password": PW})
    assert res.status_code == 409 and "Sign in" in res.json()["detail"]
    # the password rules apply
    res = TestClient(fresh).post("/v1/auth/join", json={"token": token, "email": "dee@example.com", "password": "short"})
    assert res.status_code == 400


def test_a_join_link_grants_its_role(fresh):
    c = first_run(fresh)
    link = c.post("/v1/org/join-link", json={"role": "admin"}).json()["link"]
    bo = join(fresh, link, "bo@example.com")
    assert bo.get("/v1/auth/session").json()["org"]["role"] == "admin"
    assert bo.get("/v1/org/invites").status_code == 200  # an admin's route
    assert c.post("/v1/org/join-link", json={"role": "owner"}).status_code == 422  # never owners


def test_new_disable_and_expiry(fresh):
    c = first_run(fresh)
    old = c.post("/v1/org/join-link", json={"role": "member"}).json()
    new = c.post("/v1/org/join-link", json={"role": "admin", "days": 30}).json()
    assert (datetime.fromisoformat(new["expires_at"]) - datetime.fromisoformat(new["created_at"])).days == 30
    anon = TestClient(fresh)
    assert anon.post("/v1/auth/join/lookup", json={"token": old["link"].split("#")[1]}).status_code == 404  # making a new one ends the old
    assert anon.post("/v1/auth/join", json={"token": old["link"].split("#")[1], "email": "bo@example.com", "password": PW}).status_code == 404
    assert c.get("/v1/org/join-link").json()["id"] == new["id"]
    assert c.post("/v1/org/join-link", json={"days": 91}).status_code == 422

    token = new["link"].split("#")[1]
    assert c.delete("/v1/org/join-link").status_code == 204
    assert c.get("/v1/org/join-link").json() is None
    assert anon.post("/v1/auth/join/lookup", json={"token": token}).status_code == 404
    assert anon.post("/v1/auth/join", json={"token": token, "email": "bo@example.com", "password": PW}).status_code == 404

    late = c.post("/v1/org/join-link", json={}).json()
    with db(c) as d, d.begin():
        row = d.get(JoinLink, late["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        d.add(row)
    assert anon.post("/v1/auth/join/lookup", json={"token": late["link"].split("#")[1]}).status_code == 404
    assert c.get("/v1/org/join-link").json() is None
    with db(c) as d:
        assert [u.email for u in d.exec(select(User))] == ["ana@example.com"]  # nobody got in


def test_only_owners_and_admins_manage_the_join_link(fresh):
    c = first_run(fresh)
    bo = join(fresh, c.post("/v1/org/join-link", json={}).json()["link"], "bo@example.com")
    for res in (bo.get("/v1/org/join-link"), bo.post("/v1/org/join-link", json={}), bo.delete("/v1/org/join-link")):
        assert res.status_code == 403


def test_joining_signed_in_keeps_your_other_teams(fresh, files):
    ana = first_run(fresh)
    # Bo runs his own team
    bo = join(fresh, ana.post("/v1/org/join-link", json={}).json()["link"], "bo@example.com")
    made = bo.post("/v1/orgs", json={"name": "Bo's Team"}).json()
    assert made["workspace"] == "bo-s-team"
    bo.put(f"/v1/workspaces/{made['workspace']}/files", json={"files": files})
    # Cy's team makes a link; Bo, signed in, opens it
    cy_link = ana.post("/v1/orgs", json={"name": "Cy Co"}).json()
    link = ana.post("/v1/org/join-link", json={"role": "admin"}).json()["link"]
    token = link.split("#")[1]
    info = bo.post("/v1/auth/join/lookup", json={"token": token}).json()
    assert info["org_name"] == "Cy Co" and not info["member"]
    state = bo.post("/v1/auth/join/accept", json={"token": token})
    assert state.status_code == 200, state.text
    me = state.json()
    assert me["org"]["id"] == cy_link["org"]["id"] and me["org"]["role"] == "admin" and me["org"]["workspace"] == "cy-co"
    assert sorted(o["name"] for o in me["orgs"]) == ["Bo's Team", "Cy Co", "Northwind"]  # nothing taken away
    assert bo.get(f"/v1/workspaces/{made['workspace']}/files").json()["files"]  # Bo's team's files are all there
    assert bo.post("/v1/auth/join/lookup", json={"token": token}).json()["member"]
    # joining again changes nothing, and the role stays
    assert bo.post("/v1/auth/join/accept", json={"token": token}).json()["org"]["role"] == "admin"
    assert TestClient(fresh).post("/v1/auth/join/accept", json={"token": token}).status_code == 401
    ana.delete("/v1/org/join-link")
    assert bo.post("/v1/auth/join/accept", json={"token": token}).status_code == 404


def test_joins_are_rate_limited(fresh):
    c = first_run(fresh)
    link = c.post("/v1/org/join-link", json={}).json()["link"]
    with db(c) as d, d.begin():
        for _ in range(30):
            d.add(LoginAttempt(id=str(uuid.uuid4()), email="x@example.com", ip="testclient", created_at=datetime.now(timezone.utc)))
    res = TestClient(fresh).post("/v1/auth/join", json={"token": link.split("#")[1], "email": "bo@example.com", "password": PW})
    assert res.status_code == 429


# --- platforms ---------------------------------------------------------------------------------


def test_platforms_get_and_set(fresh):
    c = first_run(fresh)
    assert c.get("/v1/auth/session").json()["platforms"] == {"selected": None, "shown": ALL}  # nobody picked: all of them
    res = c.patch("/v1/org/platforms", json={"platforms": ["vapi", "livekit", "vapi"]})
    assert res.status_code == 200 and res.json() == {"selected": ["livekit", "vapi"], "shown": ["livekit", "vapi"]}
    assert c.get("/v1/auth/session").json()["platforms"]["shown"] == ["livekit", "vapi"]
    assert c.patch("/v1/org/platforms", json={"platforms": ["other"]}).json() == {"selected": ["other"], "shown": ALL}  # not sure yet: all
    assert c.patch("/v1/org/platforms", json={"platforms": ["retell"]}).status_code == 422
    assert c.patch("/v1/org/platforms", json={"platforms": []}).status_code == 422
    assert TestClient(fresh).get("/v1/auth/session").json()["platforms"] is None  # not signed in: nothing about the team


def test_anyone_makes_the_first_pick_then_owners_and_admins(fresh):
    c = first_run(fresh)
    bo = join(fresh, c.post("/v1/org/join-link", json={}).json()["link"], "bo@example.com")
    assert bo.patch("/v1/org/platforms", json={"platforms": ["elevenlabs"]}).status_code == 200
    res = bo.patch("/v1/org/platforms", json={"platforms": ["vapi"]})
    assert res.status_code == 403 and "owners and admins" in res.json()["detail"]
    assert c.patch("/v1/org/platforms", json={"platforms": ["vapi"]}).status_code == 200
    bo.headers.pop(access.CSRF_HEADER)
    assert bo.patch("/v1/org/platforms", json={"platforms": ["vapi"]}).status_code == 403  # CSRF


def test_platforms_belong_to_each_team(fresh):
    c = first_run(fresh)
    c.patch("/v1/org/platforms", json={"platforms": ["livekit"]})
    c.post("/v1/orgs", json={"name": "Second"})
    assert c.get("/v1/auth/session").json()["platforms"] == {"selected": None, "shown": ALL}


def test_platforms_with_sign_in_off(client):
    assert client.get("/v1/auth/session").json()["platforms"] == {"selected": None, "shown": ALL}
    assert client.patch("/v1/org/platforms", json={"platforms": ["elevenlabs"]}).status_code == 200
    assert client.get("/v1/auth/session").json()["platforms"] == {"selected": ["elevenlabs"], "shown": ["elevenlabs"]}
    assert client.post("/v1/org/setup", json={"team": "Northwind"}).status_code == 409  # no teams to set up without sign-in
    assert client.post("/v1/orgs", json={"name": "Northwind"}).status_code == 409


def test_the_migration_leaves_existing_orgs_showing_every_platform(tmp_path, files):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    Store(url, Sopc(SOPC_BIN)).replace("demo", files)
    engine = create_engine(url)
    downgrade(engine, "0025")
    with engine.connect() as conn:
        assert "platforms" not in [c[1] for c in conn.execute(text("PRAGMA table_info(orgs)"))]
        assert "join_links" not in [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
    upgrade(engine)
    with Session(engine) as d:
        assert [o.platforms for o in d.exec(select(Org))] == [None]
    with engine.connect() as conn:
        assert "join_links" in [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]


# --- teams and their workspace -----------------------------------------------------------------


def test_a_team_works_in_its_most_recently_changed_workspace(fresh, files):
    c = first_run(fresh)
    org = c.get("/v1/auth/session").json()["org"]["id"]
    with db(c) as d, d.begin():
        for ws in ("alpha", "zulu"):
            d.add(Workspace(workspace=ws, org_id=org))
    assert c.get("/v1/auth/session").json()["org"]["workspace"] == "alpha"  # none changed: the first by name
    c.put("/v1/workspaces/zulu/files", json={"files": files})
    assert c.get("/v1/auth/session").json()["org"]["workspace"] == "zulu"
    with db(c) as d, d.begin():
        for v in d.exec(select(FileVersion).where(FileVersion.workspace == "zulu")):
            v.created_at = datetime.now(timezone.utc) - timedelta(days=1)
            d.add(v)
    c.put("/v1/workspaces/northwind/files", json={"files": files})
    assert c.get("/v1/auth/session").json()["org"]["workspace"] == "northwind"
    assert sorted(w["workspace"] for w in c.get("/v1/workspaces").json()) == ["alpha", "northwind", "zulu"]  # the others are still there


def test_team_names_take_a_suffix_when_taken(fresh):
    c = first_run(fresh)
    second = c.post("/v1/orgs", json={"name": "Northwind"}).json()
    third = c.post("/v1/orgs", json={"name": "northwind!"}).json()
    assert (second["workspace"], second["org"]["slug"]) == ("northwind-2", "northwind-2")
    assert (third["workspace"], third["org"]["slug"]) == ("northwind-3", "northwind-3")
    assert c.post("/v1/orgs", json={"name": "!!!"}).json()["workspace"] == "team"


def test_making_a_team_from_the_account_menu(fresh):
    c = first_run(fresh)
    res = c.post("/v1/orgs", json={"name": "Night Shift"})
    assert res.status_code == 201, res.text
    made = res.json()
    assert made["org"]["name"] == "Night Shift" and made["org"]["role"] == "owner" and made["workspace"] == "night-shift"
    me = c.get("/v1/auth/session").json()
    assert me["org"]["id"] == made["org"]["id"] and me["org"]["workspace"] == "night-shift"  # working in it now
    assert {o["name"]: o["role"] for o in me["orgs"]} == {"Northwind": "owner", "Night Shift": "owner"}
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["night-shift"]
    with db(c) as d:
        assert d.exec(select(Workspace).where(Workspace.workspace == "night-shift")).one().org_id == made["org"]["id"]
        assert len(d.exec(select(Membership)).all()) == 2


def test_switching_teams_says_where_their_workspace_is(fresh):
    c = first_run(fresh)
    first = c.get("/v1/auth/session").json()["org"]["id"]
    c.post("/v1/orgs", json={"name": "Night Shift"})
    switched = c.put("/v1/auth/org", json={"org_id": first}).json()
    assert switched["org"]["name"] == "Northwind" and switched["org"]["workspace"] == "northwind"
    assert {o["name"]: o["workspace"] for o in switched["orgs"]} == {"Northwind": "northwind", "Night Shift": "night-shift"}
    assert [w["workspace"] for w in c.get("/v1/workspaces").json()] == ["northwind"]


# --- open sign-up -------------------------------------------------------------------------------


def sign_up(app, email: str = "bo@example.com", team: str = "Night Shift", name: str = "Bo", pw: str = PW) -> httpx.Response:
    return TestClient(app).post("/v1/auth/sign-up", json={"name": name, "email": email, "password": pw, "team": team})


def test_sign_up_is_on_until_it_is_turned_off(fresh, monkeypatch):
    """Anyone can sign up where passwords are the way in; SOPOPS_OPEN_SIGNUP=0 is how a team closes it."""
    c = first_run(fresh)
    assert TestClient(fresh).get("/v1/auth/session").json()["open_signup"]
    assert sign_up(fresh).status_code == 200

    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "0")
    assert not TestClient(fresh).get("/v1/auth/session").json()["open_signup"]
    res = TestClient(fresh).post("/v1/auth/sign-up", json={"name": "Cam", "email": "cam@example.com", "password": PW, "team": "Late Shift"})
    assert res.status_code == 404 and "join link" in res.json()["detail"]
    with db(c) as d:
        assert d.exec(select(User).where(User.email == "cam@example.com")).first() is None


def test_open_sign_up_makes_an_owner_of_a_new_team(fresh, monkeypatch):
    c = first_run(fresh)
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    anon = TestClient(fresh)
    state = anon.get("/v1/auth/session").json()
    assert state["open_signup"] and not state["first_account"]
    res = anon.post("/v1/auth/sign-up", json={"name": "Bo", "email": "Bo@Example.com", "password": PW, "team": "Night Shift"})
    assert res.status_code == 200, res.text
    me = anon.get("/v1/auth/session").json()
    assert me["user"]["email"] == "bo@example.com" and me["user"]["name"] == "Bo" and me["user"]["has_password"]
    assert me["org"]["name"] == "Night Shift" and me["org"]["role"] == "owner" and me["org"]["workspace"] == "night-shift"
    assert [o["name"] for o in me["orgs"]] == ["Night Shift"]  # only the new team, not Ana's
    with_csrf(anon, None)
    assert [w["workspace"] for w in anon.get("/v1/workspaces").json()] == ["night-shift"]
    assert anon.get("/v1/workspaces/northwind/config").status_code == 404
    assert anon.patch("/v1/org/platforms", json={"platforms": ["vapi"]}).status_code == 200  # setup goes on as the first run's
    assert anon.post("/v1/org/join-link", json={}).status_code == 201
    # Ana's team is as it was
    assert [m["email"] for m in c.get("/v1/org/members").json()] == ["ana@example.com"]
    # and Bo signs in with that password
    res = TestClient(fresh).post("/v1/auth/password/login", json={"email": "bo@example.com", "password": PW})
    assert res.status_code == 200 and res.json()["org"]["name"] == "Night Shift"


def test_sign_up_team_names_take_a_suffix_when_taken(fresh, monkeypatch):
    first_run(fresh)
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "1")
    made = sign_up(fresh, team="Northwind").json()
    assert made["org"]["name"] == "Northwind" and made["org"]["slug"] == "northwind-2" and made["org"]["workspace"] == "northwind-2"
    assert sign_up(fresh, email="cy@example.com", team="  ").status_code in (400, 422)


def test_sign_up_with_an_address_that_has_an_account(fresh, monkeypatch):
    c = first_run(fresh)
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    res = sign_up(fresh, email="ANA@example.com")
    assert res.status_code == 409 and "Sign in instead" in res.json()["detail"]
    assert sign_up(fresh, pw="short").status_code == 400  # the password rules apply
    with db(c) as d:
        assert len(d.exec(select(Org)).all()) == 1


def test_sign_up_leaves_addresses_an_invite_or_a_domain_waits_for(fresh, monkeypatch):
    c = first_run(fresh)
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    assert c.post("/v1/org/invites", json={"email": "dee@example.com"}).status_code == 201
    res = sign_up(fresh, email="dee@example.com")
    assert res.status_code == 409 and "join link" in res.json()["detail"]
    assert c.patch("/v1/org", json={"allowed_domains": ["acme.com"]}).status_code == 200
    assert sign_up(fresh, email="eve@acme.com").status_code == 409
    assert sign_up(fresh, email="eve@other.com").status_code == 200


def test_sign_up_on_a_fresh_install_is_the_first_run(fresh, monkeypatch):
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    assert TestClient(fresh).get("/v1/auth/session").json()["first_account"]
    assert sign_up(fresh).status_code == 409


def test_sign_ups_are_rate_limited(fresh, monkeypatch):
    c = first_run(fresh)
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    with db(c) as d, d.begin():
        for _ in range(30):
            d.add(LoginAttempt(id=str(uuid.uuid4()), email="x@example.com", ip="testclient", created_at=datetime.now(timezone.utc)))
    assert sign_up(fresh).status_code == 429


def test_sign_up_needs_sign_in_on(client, monkeypatch):
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    assert not client.get("/v1/auth/session").json()["open_signup"]
    assert client.post("/v1/auth/sign-up", json={"email": "bo@example.com", "password": PW, "team": "T"}).status_code == 404
