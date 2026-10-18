"""Workspace API tokens: one model for every token, with scopes, expiry and revocation.

| Scope | Lets the token | Made by |
|---|---|---|
| `ingest` | send sessions (`POST …/sessions`) | Settings → Tokens |
| `runtime` | fetch prompts, report served calls, authenticate call-start webhooks | Settings → Tokens |
| `onboarding:write` | push an import's proposal and progress (one import, 2 hours) | Import agents |

Tokens are stored as a sha256 hash and shown once, when made. A revoked token is kept, with `revoked_at`, so the
list shows what existed. Prompt fetches are open while a workspace has no unrevoked `runtime` token (local mode);
an expired one still closes them, so expiry never opens access.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable

from sqlmodel import Session, select

from .errors import NotFound, workspace_id
from .orm import Token

if TYPE_CHECKING:
    from .store import Store

INGEST, RUNTIME, ONBOARDING = "ingest", "runtime", "onboarding:write"
SCOPES = (INGEST, RUNTIME, ONBOARDING)
USER_SCOPES = (INGEST, RUNTIME)  # what Settings makes; onboarding tokens come from the import flow
PREFIX = "sopops_"


class Unauthorized(Exception):
    """401: no token, an unknown one, or one that's expired, revoked or for another workspace."""


class Forbidden(Exception):
    """403: a valid token without the scope."""


class Tokens:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- making and listing ------------------------------------------------------

    def create(
        self,
        workspace: str,
        scopes: Iterable[str],
        name: str = "",
        expires_at: datetime | None = None,
        import_id: str | None = None,
        created_by: str | None = None,
        prefix: str = PREFIX,
    ) -> dict:
        """A new token. The token itself is only returned here."""
        ws = workspace_id(workspace)
        scopes = sorted(set(scopes), key=SCOPES.index)
        if not scopes or any(s not in SCOPES for s in scopes):
            raise ValueError(f"scopes must be some of {', '.join(SCOPES)}")
        token = prefix + secrets.token_urlsafe(32)
        row = Token(
            id=str(uuid.uuid4()), workspace=ws, name=(name or "").strip()[:128], token_hash=_hash(token), prefix=token[: len(prefix) + 6],
            scopes=list(scopes), import_id=import_id, created_by=created_by, created_at=_now(), expires_at=expires_at,
        )
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            db.add(row)
        return {**out(row), "token": token}

    def list(self, workspace: str, imports: bool = False) -> list[dict]:
        """The workspace's tokens, newest first. Onboarding tokens (tied to an import) only with `imports`."""
        with self._db() as db:
            q = select(Token).where(Token.workspace == workspace_id(workspace))
            if not imports:
                q = q.where(Token.import_id.is_(None))  # type: ignore[union-attr]
            return [out(t) for t in db.exec(q.order_by(Token.created_at.desc()))]  # type: ignore[attr-defined]

    def revoke(self, workspace: str, id: str) -> dict:
        with self._db() as db, db.begin():
            t = db.get(Token, id)
            if t is None or t.workspace != workspace_id(workspace):
                raise NotFound(f"no token '{id}'")
            if t.revoked_at is None:
                t.revoked_at = _now()
                db.add(t)
        return out(t)

    def for_import(self, import_id: str, db: Session | None = None) -> Token | None:
        def find(d: Session) -> Token | None:
            return d.exec(select(Token).where(Token.import_id == import_id).order_by(Token.created_at.desc())).first()  # type: ignore[attr-defined]

        if db is not None:
            return find(db)
        with self._db() as d:
            return find(d)

    def revoke_import(self, db: Session, import_id: str) -> None:
        """End an import's onboarding token, in the caller's transaction."""
        for t in db.exec(select(Token).where(Token.import_id == import_id, Token.revoked_at.is_(None))):  # type: ignore[union-attr]
            t.revoked_at = _now()
            db.add(t)

    # --- checking ----------------------------------------------------------------

    def guarded(self, workspace: str, scope: str) -> bool:
        """Whether the workspace has an unrevoked token with this scope (expired ones count)."""
        with self._db() as db:
            rows = db.exec(select(Token).where(Token.workspace == workspace_id(workspace), Token.revoked_at.is_(None)))  # type: ignore[union-attr]
            return any(scope in (t.scopes or []) for t in rows)

    def check(self, workspace: str | None, authorization: str | None, scope: str) -> Token:
        """The token in `authorization` (`Bearer <token>`), if it's live, for `workspace` (any, if None) and has
        `scope`. Raises Unauthorized (401) or Forbidden (403)."""
        ws = workspace_id(workspace) if workspace is not None else None
        scheme, _, raw = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            raise Unauthorized(f"send a token with the {scope} scope as 'Authorization: Bearer <token>'{_where(scope)}")
        with self._db() as db:
            t = db.exec(select(Token).where(Token.token_hash == _hash(raw.strip()))).first()
        if t is None or (ws is not None and t.workspace != ws):
            if ws is not None and not self.guarded(ws, scope):
                raise Unauthorized(f"workspace '{ws}' has no token with the {scope} scope{_where(scope)}")
            raise Unauthorized("invalid token")
        if t.revoked_at is not None:
            raise Unauthorized(f"this token was revoked{_where(scope)}")
        if t.expires_at is not None and _utc(t.expires_at) <= _now():
            raise Unauthorized(f"this token expired{_where(scope)}")
        if scope not in (t.scopes or []):
            raise Forbidden(f"this token can't do that (it needs the {scope} scope)")
        return t


def active(t: Token) -> bool:
    return t.revoked_at is None and (t.expires_at is None or _utc(t.expires_at) > _now())


def out(t: Token) -> dict:
    return {
        "id": t.id, "workspace": t.workspace, "name": t.name, "prefix": t.prefix, "scopes": list(t.scopes or []), "import_id": t.import_id,
        "created_by": t.created_by, "created_at": _utc(t.created_at), "expires_at": _utc(t.expires_at) if t.expires_at else None,
        "revoked_at": _utc(t.revoked_at) if t.revoked_at else None, "active": active(t),
    }


def expiry(days: int | None) -> datetime | None:
    return _now() + timedelta(days=days) if days else None


def _where(scope: str) -> str:
    return "; create one in sopops on the Import agents page" if scope == ONBOARDING else "; create one in Settings → Tokens"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
