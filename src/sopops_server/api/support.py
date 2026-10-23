"""Support sessions (`/v1/support`), and what a team sees of them (`/v1/org/support`).

Support staff — the addresses in SOPOPS_SUPPORT_EMAILS, and nobody else — can work in one customer's workspace to
help them, as a member of their team: reading their SOPs, their sessions and their call transcripts, and changing
them. So it is logged for the customer to read, it ends by itself, and the team can turn it off altogether.

| Route | |
|---|---|
| `GET /v1/support/teams` | The teams a session can be started against, with their workspaces and whether each allows one. Staff only. |
| `POST /v1/support/sessions` | Start one, against an org id or a workspace. Sets the `sopops_mimic` cookies; the staff member's own session stays as it is. |
| `DELETE /v1/support/session` | End it: the cookies go and the audit row is closed. |
| `GET /v1/support/session` | What the banner shows: whether one is running, against whom, until when, and whether you're staff at all. |
| `GET /v1/org/support` | Your team's: whether support access is allowed, and every session against you. |
| `PATCH /v1/org/support` | Turn support access on or off. Owners and admins. |

With SOPOPS_SUPPORT_EMAILS unset or empty (the default) the feature is off and every route here answers 404, as if it
weren't there. The rules themselves are in `sopops_server.auth.support` and `sopops_server.auth.access`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from ..auth import access, mode
from ..auth.service import Current
from ..auth.support import Support, enabled, off
from ..errors import Conflict
from ..schemas.support import OrgSupportIn, OrgSupportOut, SupportStarted, SupportStartIn, SupportState, SupportTeamOut
from ..store import Store
from ..tokens import Forbidden, Unauthorized

log = logging.getLogger("sopops.support")

MISSING = {404: {"description": "Support sessions aren't set up on this sopops (SOPOPS_SUPPORT_EMAILS)."}}


def router(store: Store) -> APIRouter:
    r = APIRouter(tags=["support"])
    support = Support(store)

    def here() -> None:
        """404 for everything here when the feature is off, so it looks like it doesn't exist."""
        if not enabled():
            raise off()

    def login(request: Request) -> Current:
        cur = access.signed_in(request)
        if cur is None:
            if not mode.required():
                raise HTTPException(409, "sign-in is off on this sopops: there's nobody to support")
            raise Unauthorized("sign in first")
        return cur

    def org_of(request: Request) -> tuple[Current, str]:
        cur = login(request)
        if cur.org_id is None:
            raise Forbidden(access.NO_MEMBERSHIP)
        return cur, cur.org_id

    @r.post("/v1/support/sessions", response_model=SupportStarted, status_code=201,
            responses={**MISSING, 403: {"description": "The team has turned support access off."},
                       409: {"description": "You're in a support session already."}})
    def start_support_session(req: SupportStartIn, request: Request, response: Response) -> dict:
        """Start a support session against a customer, to help them: you work in their workspace, their sessions
        and their call transcripts as one of their members would. It ends at the time returned, at the latest. Your
        own sign-in stays as it is underneath."""
        here()
        cur = login(request)
        try:
            out, token = support.start(cur, req.org, req.reason, req.minutes)
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        log.info("user %s started a support session on org %s until %s: %s", cur.user.id, out["org_id"], out["until"], req.reason)
        access.set_mimic_cookies(response, request, token, req.minutes * 60)
        return out

    @r.delete("/v1/support/session", status_code=204, responses=MISSING)
    def end_support_session(request: Request) -> Response:
        """End the support session: its cookies go and it stops working at once. You're back in your own account,
        signed in as before."""
        here()
        login(request)
        support.end(request.cookies.get(access.MIMIC_COOKIE))
        out = Response(status_code=204)
        access.clear_mimic_cookies(out, request)
        return out

    @r.get("/v1/support/session", response_model=SupportState, responses=MISSING)
    def get_support_state(request: Request) -> dict:
        """Whether you're in a customer's workspace right now, whose and until when, and whether you're support
        staff at all. The band the customer's data is shown under comes from this."""
        here()
        return support.state(login(request))

    @r.get("/v1/support/teams", response_model=list[SupportTeamOut], responses=MISSING)
    def list_support_teams(request: Request) -> list[dict]:
        """The teams you can start a support session against: each one's id, name, workspaces, and whether it allows
        support sessions at all. Staff only — anyone else gets the same 404 as the feature being off."""
        here()
        cur = login(request)
        if not support.is_staff(cur.user):
            raise off()  # the same answer as the feature being off: no hint that there's a staff list to get onto
        return support.teams()

    @r.get("/v1/org/support", response_model=OrgSupportOut, responses=MISSING)
    def get_org_support(request: Request) -> dict:
        """Who from support has opened a session on your team's workspace, when, how long for and how much they
        did; and whether support access is allowed at all."""
        here()
        _, org_id = org_of(request)
        return {"support_access": support.access(org_id), "sessions": support.sessions(org_id)}

    @r.patch("/v1/org/support", response_model=OrgSupportOut, responses=MISSING)
    def set_org_support(req: OrgSupportIn, request: Request) -> dict:
        """Allow or refuse support sessions against your team. Off, nobody from support can start one; a session
        already running still ends at its own time. Owners and admins."""
        here()
        cur, org_id = org_of(request)
        support.set_access(org_id, req.support_access)
        log.info("user %s set support access for org %s to %s", cur.user.id, org_id, req.support_access)
        return {"support_access": req.support_access, "sessions": support.sessions(org_id)}

    return r
