"""The procedures a session is checked against: from the release it ran on, else the workspace's live release.

Rules come from `sopc export`, with sopc's stable ids. When the session's agent is known, only the procedures it
lists (directly or through a group) are candidates; otherwise every procedure in the release is.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from ..models import FileVersion, Release, Workspace
from ..sopc import Invalid

if TYPE_CHECKING:
    from ..models import SessionRecord
    from ..store import Store


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str  # step | never | warning
    index: int  # 1-based within its kind
    text: str  # as written, with {{placeholders}}
    tool: str | None = None
    required: bool = False

    @property
    def key(self) -> str:
        """The field name a check reply uses for this rule: step_1, never_2, warning_1."""
        return f"{self.kind}_{self.index}"

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    goal: str
    when: str
    guidance: str
    rules: tuple[Rule, ...]
    version: int | None
    release: int

    def steps(self) -> list[Rule]:
        return [r for r in self.rules if r.kind == "step"]


@dataclass
class Candidates:
    release: int | None
    procedures: list[Procedure] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


_cache: dict[tuple[str, int], dict[str, Any]] = {}
_lock = threading.Lock()


def candidates(store: Store, session: SessionRecord) -> Candidates:
    with Session(store.engine) as db:
        release = session.release_id or db.exec(select(Workspace.current_release).where(Workspace.workspace == session.workspace_id)).first()
        if release is None:
            return Candidates(None)
        exported = _release_export(store, db, session.workspace_id, release)
    if exported is None:
        return Candidates(release)
    sops = {s["id"]: s for s in exported["export"].get("sops", [])}
    config = exported["export"].get("config") or {}
    agents = {a["id"]: a for a in exported["export"].get("agents", [])}
    variables = {k: str(v) for k, v in (config.get("variables") or {}).items()}
    agent = agents.get(session.agent_id or "")
    if agent is not None:
        variables.update({k: str(v) for k, v in (agent.get("variables") or {}).items()})
        ids = [b for b in _expand(agent.get("blocks") or [], config.get("groups") or {}) if b in sops]
    else:
        ids = list(sops)
    versions = exported["versions"]
    return Candidates(release, [_procedure(sops[i], versions, release) for i in dict.fromkeys(ids)], variables)


def fill(text: str, variables: dict[str, str]) -> str:
    """{{placeholders}} replaced with the agent's values, where known."""
    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", lambda m: variables.get(m.group(1), m.group(0)), text)


def _expand(blocks: list[str], groups: dict[str, list[str]], seen: frozenset[str] = frozenset()) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if b in groups and b not in seen:
            out += _expand(groups[b], groups, seen | {b})
        else:
            out.append(b)
    return out


def _procedure(sop: dict[str, Any], versions: dict[str, int], release: int) -> Procedure:
    steps = sop.get("procedureSteps") or []
    rules = []
    for r in sop.get("rules") or []:
        tool, required = None, False
        if r["kind"] == "step" and 0 < r["index"] <= len(steps) and isinstance(steps[r["index"] - 1], dict):
            tool, required = steps[r["index"] - 1].get("tool"), bool(steps[r["index"] - 1].get("required"))
        rules.append(Rule(r["id"], r["kind"], int(r["index"]), r["text"], tool, required))
    return Procedure(
        id=sop["id"],
        name=sop.get("name") or sop["id"],
        goal=sop.get("description") or "",
        when=sop.get("scope") or "",
        guidance=(sop.get("guidance") or "").strip(),
        rules=tuple(rules),
        version=versions.get(sop.get("file", "")),
        release=release,
    )


def _release_export(store: Store, db: Session, ws: str, number: int) -> dict[str, Any] | None:
    """`sopc export` of a release's files, cached for good (releases are immutable)."""
    key = (ws, number)
    with _lock:
        if key in _cache:
            return _cache[key]
    rel = db.get(Release, (ws, number))
    if rel is None:
        return None
    files = {}
    for path, version in rel.files.items():
        content = db.exec(select(FileVersion.content).where(FileVersion.workspace == ws, FileVersion.path == path, FileVersion.version == version)).first()
        if content is not None:
            files[path] = content
    try:
        export = store.sopc.export(files)
    except Invalid:  # a release in an older sopc format
        export = store.sopc.export(store.sopc.migrate(files)["files"])
    out = {"export": export, "versions": dict(rel.files)}
    with _lock:
        _cache[key] = out
    return out
