"""Support sessions: who may start one, the team's consent, working in a customer's workspace, the teams a session
can be started against, the session dying at its own time, ending it, and the audit trail the customer reads."""

from datetime import datetime, timedelta, timezone

import pytest
from conftest import WS
from fastapi.testclient import TestClient
from sqlmodel import select
from test_auth import app, db, owner, providers_env, sign_in  # noqa: F401  (fixtures and helpers)

from sopops_server.auth import access, support
from sopops_server.auth.service import csrf_token
from sopops_server.orm import LoginSession, Org, SupportSession, User, Workspace

STAFF = "sam@sopops.example"
PW = "correct horse battery"


@pytest.fixture
def staff_env(monkeypatch):
    """This sopops has support staff: two addresses, one of them Sam's."""
    monkeypatch.setenv(support.STAFF_ENV, f" pat@sopops.example, {STAFF} ")


def signed_in(app, email: str, id: str = "90") -> TestClient:
    """Someone signed in with GitHub who's in no org (not the first user, no invite, no allowed domain)."""
    c = TestClient(app)
    sign_in(c, "github", id, email)
    return c


def org_of(owner) -> str:
    return owner.get("/v1/auth/session").json()["org"]["id"]


def start(c: TestClient, org: str, reason: str = "ticket 412: their SOP won't publish", minutes: int = 60):
    res = c.post("/v1/support/sessions", json={"org": org, "reason": reason, "minutes": minutes})
    if res.status_code == 201:  # from now on the client sends the support session's CSRF token, as the web UI does
        c.headers[access.CSRF_HEADER] = c.cookies.get(access.MIMIC_CSRF_COOKIE)
    return res


def test_only_an_address_in_the_setting_can_start_one(owner, app, staff_env):
    org = org_of(owner)
    nobody = signed_in(app, "mia@example.net", "11")
    assert start(nobody, org).status_code == 404  # the same answer as the feature being off: no hint of a staff list
    assert start(owner, org).status_code == 404  # the customer's own owner isn't staff either
    assert nobody.get("/v1/support/session").json() == {"running": False, "staff": False, "org_id": None, "org_name": None, "until": None}
    sam = signed_in(app, STAFF)
    assert sam.get("/v1/support/session").json()["staff"] is True
    assert start(sam, org).status_code == 201


def test_without_the_setting_the_routes_arent_there(owner, app):
    assert not support.enabled()
    sam = signed_in(app, STAFF)
    assert start(sam, org_of(owner)).status_code == 404
    assert sam.get("/v1/support/session").status_code == 404
    assert sam.delete("/v1/support/session").status_code == 404
    assert sam.get("/v1/support/teams").status_code == 404
    assert owner.get("/v1/org/support").status_code == 404
    assert owner.patch("/v1/org/support", json={"support_access": False}).status_code == 404


def test_a_team_that_turned_support_access_off_refuses(owner, app, staff_env):
    org = org_of(owner)
    assert owner.get("/v1/org/support").json() == {"support_access": True, "sessions": []}
    assert owner.patch("/v1/org/support", json={"support_access": False}).json()["support_access"] is False
    sam = signed_in(app, STAFF)
    res = start(sam, org)
    assert res.status_code == 403 and "turned support access off" in res.json()["detail"]
    assert owner.patch("/v1/org/support", json={"support_access": True}).status_code == 200
    assert start(sam, org).status_code == 201


def test_only_owners_and_admins_turn_support_access_off(owner, app, staff_env):
    token = owner.post("/v1/org/invites", json={"email": "mia@example.com", "role": "member"}).json()["link"]
    member = signed_in(app, "mia@example.com", "11")
    assert member.get("/v1/auth/session").json()["org"]["role"] == "member", token
    assert member.get("/v1/org/support").status_code == 200  # any member sees who looked at their data
    assert member.patch("/v1/org/support", json={"support_access": False}).status_code == 403


def test_a_support_session_works_in_the_customers_workspace(owner, app, staff_env):
    """Not read-only: the customer hands support the keys, so a session edits their SOPs as a member would."""
    org, name = org_of(owner), owner.get("/v1/org").json()["name"]
    sam = signed_in(app, STAFF)
    assert sam.get(f"{WS}/files").status_code == 404  # before: Sam is in no org, so the workspace isn't there for him
    res = start(sam, "demo")  # a workspace names its org just as well as the org id does
    assert res.status_code == 201
    assert res.json()["org_id"] == org and res.json()["org_name"] == name

    state = sam.get("/v1/support/session").json()
    assert state["running"] is True and state["org_id"] == org and state["org_name"] == name and state["until"] == res.json()["until"]
    assert sam.get(f"{WS}/files").status_code == 200  # the customer's SOPs
    assert sam.get(f"{WS}/sessions").status_code == 200  # and their calls, transcripts and all
    assert [w["workspace"] for w in sam.get("/v1/workspaces").json()] == ["demo"]

    wrote = sam.post(f"{WS}/files", json={"changes": {"instructions/greeting.md": "Hi."}})
    assert wrote.status_code in (200, 201), wrote.text
    assert owner.get(f"{WS}/files/instructions/greeting.md").json()["content"] == "Hi."  # the customer has the edit
    assert sam.post(f"{WS}/releases", json={}).status_code in (200, 201)  # and support can publish for them

    # the mimic session's CSRF token is still enforced: the wrong one (Sam's own) is refused
    sam.headers[access.CSRF_HEADER] = sam.cookies.get(access.CSRF_COOKIE)
    refused = sam.post(f"{WS}/files", json={"changes": {"instructions/greeting.md": "Nope."}})
    assert refused.status_code == 403 and "CSRF" in refused.json()["detail"]
    sam.headers[access.CSRF_HEADER] = sam.cookies.get(access.MIMIC_CSRF_COOKIE)

    # one session at a time, still
    assert sam.post("/v1/support/sessions", json={"org": org, "minutes": 60}).status_code == 409


def test_admin_routes_refuse_a_support_session_because_its_role_is_member(owner, app, staff_env):
    """The session can write, but it is scoped to the org as `member`, so the org-admin routes still turn it away."""
    sam = signed_in(app, STAFF)
    assert start(sam, org_of(owner)).status_code == 201
    assert sam.get("/v1/org").json()["role"] == "member"  # whatever Sam is elsewhere
    for res in (sam.get("/v1/org/invites"), sam.patch("/v1/org", json={"name": "Mine"}),
                sam.post("/v1/org/invites", json={"email": "mia@example.com"})):
        assert res.status_code == 403 and "owners and admins" in res.json()["detail"], res.text


def test_a_workspace_in_another_org_still_answers_404(owner, app, staff_env):
    with db(owner) as d, d.begin():
        d.add(Org(id="other-org", name="Other", slug="other", allowed_domains=[], created_at=datetime.now(timezone.utc)))
        d.flush()
        d.add(Workspace(workspace="theirs", org_id="other-org"))
    sam = signed_in(app, STAFF)
    assert start(sam, org_of(owner)).status_code == 201
    assert sam.get("/v1/workspaces/theirs/files").status_code == 404
    assert sam.get(f"{WS}/files").status_code == 200


def test_it_dies_at_its_time_and_using_it_doesnt_push_that_out(owner, app, staff_env):
    sam = signed_in(app, STAFF)
    assert start(sam, org_of(owner), minutes=60).status_code == 201
    with db(owner) as d:
        row = d.exec(select(LoginSession).where(LoginSession.mimic_until.is_not(None))).one()
        until, expires = row.mimic_until, row.expires_at
    for _ in range(3):
        assert sam.get(f"{WS}/files").status_code == 200
    with db(owner) as d:  # a support session doesn't slide: using it moves neither end
        row = d.exec(select(LoginSession).where(LoginSession.mimic_until.is_not(None))).one()
        assert (row.mimic_until, row.expires_at) == (until, expires)

    with db(owner) as d, d.begin():  # time passes
        row = d.exec(select(LoginSession).where(LoginSession.mimic_until.is_not(None))).one()
        row.mimic_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=30)  # even with plenty of ordinary session left
        d.add(row)
    assert sam.get(f"{WS}/files").status_code == 404  # the cookie is dead, and Sam is in no org of his own
    assert sam.get("/v1/support/session").json() == {"running": False, "staff": True, "org_id": None, "org_name": None, "until": None}
    assert sam.get("/v1/auth/session").json()["user"]["email"] == STAFF  # still signed in as himself


def test_ending_it_clears_the_cookies_and_leaves_his_own_sign_in_alone(owner, app, staff_env):
    sam = signed_in(app, STAFF)
    own = sam.cookies.get(access.SESSION_COOKIE)
    assert start(sam, org_of(owner)).status_code == 201
    assert sam.cookies.get(access.SESSION_COOKIE) == own  # his own cookie was never touched
    assert sam.cookies.get(access.MIMIC_CSRF_COOKIE) == csrf_token(sam.cookies.get(access.MIMIC_COOKIE))

    out = sam.delete("/v1/support/session")
    assert out.status_code == 204
    cleared = {h.split("=")[0] for h in out.headers.get_list("set-cookie") if "Max-Age=0" in h}
    assert cleared == {access.MIMIC_COOKIE, access.MIMIC_CSRF_COOKIE}
    assert sam.cookies.get(access.MIMIC_COOKIE) is None and sam.cookies.get(access.SESSION_COOKIE) == own

    sam.headers[access.CSRF_HEADER] = sam.cookies.get(access.CSRF_COOKIE)
    assert sam.get("/v1/auth/session").json()["user"]["email"] == STAFF  # signed in as himself, with no sign-in in between
    assert sam.get(f"{WS}/files").status_code == 404  # and back to his own (empty) access
    assert sam.get("/v1/support/session").json()["running"] is False
    assert start(sam, org_of(owner)).status_code == 201  # and he can start another


def test_the_audit_row_is_written_counted_and_closed(owner, app, staff_env):
    org = org_of(owner)
    sam = signed_in(app, STAFF)
    reason = "ticket 412: their release won't publish"
    assert start(sam, org, reason=reason).status_code == 201
    with db(owner) as d:
        row = d.exec(select(SupportSession)).one()
        assert (row.staff_email, row.org_id, row.reason, row.ended_at) == (STAFF, org, reason, None)

    for _ in range(3):
        assert sam.get(f"{WS}/files").status_code == 200
    seen = owner.get("/v1/org/support").json()["sessions"]
    assert len(seen) == 1 and seen[0]["staff_email"] == STAFF and seen[0]["reason"] == reason and seen[0]["running"] is True
    assert seen[0]["requests"] >= 3 and seen[0]["last_path"] == f"{WS}/files"

    assert sam.delete("/v1/support/session").status_code == 204
    done = owner.get("/v1/org/support").json()["sessions"]
    assert len(done) == 1 and done[0]["ended_at"] is not None and done[0]["running"] is False  # closed, never deleted
    with db(owner) as d:
        assert d.exec(select(SupportSession)).one().ended_at is not None


def test_no_reason_is_needed_and_eight_hours_is_the_most(owner, app, staff_env):
    org = org_of(owner)
    sam = signed_in(app, STAFF)
    assert start(sam, org, minutes=481).status_code == 422
    assert start(sam, "no-such-workspace").status_code == 404
    res = sam.post("/v1/support/sessions", json={"org": org})  # no reason, no minutes: an hour by default
    assert res.status_code == 201
    started, until = datetime.now(timezone.utc), datetime.fromisoformat(res.json()["until"])
    assert timedelta(minutes=59) < until - started < timedelta(minutes=61)
    with db(owner) as d:
        assert d.exec(select(SupportSession)).one().reason == ""  # the audit row stores whatever came in, nothing included


def test_the_teams_route_lists_every_team_for_staff_only(owner, app, staff_env):
    org = org_of(owner)
    with db(owner) as d, d.begin():
        d.add(Org(id="other-org", name="Aardvark", slug="aardvark", allowed_domains=[], created_at=datetime.now(timezone.utc)))
        d.flush()
        d.add(Workspace(workspace="theirs", org_id="other-org"))
    assert owner.patch("/v1/org/support", json={"support_access": False}).status_code == 200

    sam = signed_in(app, STAFF)
    teams = sam.get("/v1/support/teams")
    assert teams.status_code == 200
    rows = {t["id"]: t for t in teams.json()}
    assert set(rows) == {org, "other-org"}
    assert rows[org]["workspaces"] == ["demo"] and rows[org]["support_access"] is False  # shown, but can't be picked
    assert rows["other-org"]["name"] == "Aardvark" and rows["other-org"]["workspaces"] == ["theirs"]
    assert rows["other-org"]["support_access"] is True

    # anyone who isn't staff gets the 404 the feature being off gives: no hint there's a staff list
    assert signed_in(app, "mia@example.net", "11").get("/v1/support/teams").status_code == 404
    assert owner.get("/v1/support/teams").status_code == 404


# --- being staff is proved, not claimed -------------------------------------------------


def test_a_password_alone_never_makes_you_staff(owner, app, staff_env):
    """A password says an owner trusted the address, not that the person typing owns it, so it doesn't make staff."""
    org = org_of(owner)
    invite = owner.post("/v1/org/invites", json={"email": STAFF}).json()["link"].split("#")[1]
    claimed = TestClient(app)
    assert claimed.post("/v1/auth/invites/join", json={"token": invite, "name": "Sam", "password": PW}).status_code == 200
    claimed.headers[access.CSRF_HEADER] = claimed.cookies.get(access.CSRF_COOKIE)
    assert claimed.get("/v1/auth/session").json()["user"]["email"] == STAFF  # the address is on the staff list
    assert claimed.get("/v1/support/session").json()["staff"] is False  # and nobody vouched for it
    assert start(claimed, org).status_code == 404

    proved = TestClient(app)  # the same address again, this time from GitHub, which owns it
    sign_in(proved, "github", "77", STAFF)
    assert proved.get("/v1/support/session").json()["staff"] is True
    assert start(proved, org).status_code == 201
    with db(owner) as d:
        assert len(d.exec(select(User).where(User.email == STAFF)).all()) == 1  # one account, two identities


def test_a_staff_address_cant_be_claimed_by_signing_up_or_a_join_link(owner, app, staff_env, monkeypatch):
    monkeypatch.setenv("SOPOPS_OPEN_SIGNUP", "true")
    taken = TestClient(app).post("/v1/auth/sign-up", json={"name": "Eve", "email": STAFF, "password": PW, "team": "Night Shift"})
    assert taken.status_code == 409
    assert TestClient(app).post("/v1/auth/sign-up", json={"name": "Eve", "email": "eve@elsewhere.example", "password": PW,
                                                          "team": "Night Shift"}).status_code == 200  # an ordinary address still can

    # and the refusal reads exactly like an address an invite is waiting for, so it doesn't point at the staff list
    assert owner.post("/v1/org/invites", json={"email": "dee@example.com"}).status_code == 201
    waited = TestClient(app).post("/v1/auth/sign-up", json={"name": "Dee", "email": "dee@example.com", "password": PW, "team": "Day Shift"})
    assert waited.status_code == 409 and waited.json()["detail"] == taken.json()["detail"]

    link = owner.post("/v1/org/join-link", json={}).json()["link"].split("#")[1]
    joined = TestClient(app).post("/v1/auth/join", json={"token": link, "name": "Eve", "email": STAFF, "password": PW})
    assert joined.status_code == 409 and "already has an account" in joined.json()["detail"]
    with db(owner) as d:
        assert d.exec(select(User).where(User.email == STAFF)).first() is None


def test_turning_support_access_off_stops_a_session_already_running(owner, app, staff_env):
    sam = signed_in(app, STAFF)
    assert start(sam, org_of(owner), minutes=60).status_code == 201
    assert sam.get(f"{WS}/files").status_code == 200
    assert owner.patch("/v1/org/support", json={"support_access": False}).json()["support_access"] is False

    assert sam.get(f"{WS}/files").status_code == 404  # the watching stops there and then, not in an hour
    assert sam.get("/v1/support/session").json()["running"] is False
    assert sam.get("/v1/auth/session").json()["user"]["email"] == STAFF  # his own sign-in is untouched
    seen = owner.get("/v1/org/support").json()["sessions"]
    assert len(seen) == 1 and seen[0]["ended_at"] is not None and seen[0]["running"] is False
    with db(owner) as d:
        assert d.exec(select(LoginSession).where(LoginSession.mimic_until.is_not(None))).one().revoked_at is not None
