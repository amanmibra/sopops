"""The procedures a session is checked against: from the release it ran on, else the workspace's live release.

Rules come from `sop export`, with sop's stable ids. When the session's agent is known, only the procedures it
lists (directly or through a group) are candidates; otherwise every procedure in the release is. Rule text keeps its
`{{placeholders}}` (rules are shared by every agent that uses the procedure); `fill` puts in the agent's values
(workspace variables, then the agent's own) wherever a model or a session's page reads it.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from ..orm import FileVersion, Release, Workspace
from ..sop import Invalid, SopUnavailable

if TYPE_CHECKING:
    from ..orm import SessionRecord
    from ..store import Store


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str  # step | option | never | warning
    index: int  # 1-based within its kind; an option has its step's
    text: str  # as written, with {{placeholders}}; a step with OR options: theirs, joined with " OR "
    tool: str | None = None
    required: bool = False
    option: int | None = None  # an option: its 1-based place among its step's options
    of: str | None = None  # an option: its step's rule id

    @property
    def key(self) -> str:
        """The field name a check reply uses for this rule: step_1, never_2, warning_1, step_3_or_2 (an option)."""
        if self.option is not None:
            return f"step_{self.index}_or_{self.option}"
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

    def has_options(self, step_id: str) -> bool:
        return any(r.of == step_id for r in self.rules)

    def judged(self) -> Procedure:
        """The procedure as the checker judges it: a step with OR options isn't judged itself (its result comes from
        its options, `pipeline.rollup.combine_options`); each option is judged as a step."""
        rules = tuple(replace(r, kind="step") if r.kind == "option" else r for r in self.rules if not (r.kind == "step" and self.has_options(r.id)))
        return replace(self, rules=rules)


@dataclass
class Candidates:
    release: int | None
    procedures: list[Procedure] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


_cache: dict[tuple[str, int, str], dict[str, Any]] = {}
_lock = threading.Lock()
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")  # names as sop reads them


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
    agent = _agent(exported, session.agent_id)
    if agent is not None:
        ids = [b for b in _expand(agent.get("blocks") or [], config.get("groups") or {}) if b in sops]
    else:
        ids = list(sops)
    versions = exported["versions"]
    return Candidates(release, [_procedure(sops[i], versions, release) for i in dict.fromkeys(ids)], _variables(exported, agent))


def agent_variables(store: Store, ws: str, release: int, agent_id: str | None, known: bool = False) -> dict[str, str]:
    """The values an agent gives `{{placeholders}}` in a release, as its sessions' checks filled them (an agent the
    release doesn't have gets the workspace's, unless `known`: then none). For showing rule text: a release sop
    can't read (or no sop) gives none, and the placeholders show as they are."""
    try:
        with Session(store.engine) as db:
            exported = _release_export(store, db, ws, release)
    except (Invalid, SopUnavailable):
        return {}
    agent = _agent(exported, agent_id) if exported is not None else None
    return _variables(exported, agent) if exported is not None and (agent is not None or not known) else {}


def fill(text: str, variables: dict[str, str]) -> str:
    """{{placeholders}} replaced with the agent's values, where known."""
    return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def _agent(exported: dict[str, Any], agent_id: str | None) -> dict[str, Any] | None:
    return next((a for a in exported["export"].get("agents", []) if a["id"] == agent_id), None) if agent_id else None


def _variables(exported: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, str]:
    config = exported["export"].get("config") or {}
    variables = {k: str(v) for k, v in (config.get("variables") or {}).items()}
    if agent is not None:
        variables.update({k: str(v) for k, v in (agent.get("variables") or {}).items()})
    return variables


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
        step = steps[r["index"] - 1] if r["kind"] in ("step", "option") and 0 < r["index"] <= len(steps) else None
        if isinstance(step, dict):
            # option 1 is the step's own text, tool and required, the rest its `or` list; a step with options has no tool
            others = step.get("or") or []
            if r["kind"] == "option":
                source = step if r["option"] == 1 else others[r["option"] - 2] if r["option"] - 2 < len(others) else None
            else:
                source = None if others else step
            if isinstance(source, dict):
                tool, required = source.get("tool"), bool(source.get("required"))
        rules.append(Rule(r["id"], r["kind"], int(r["index"]), r["text"], tool, required, r.get("option"), r.get("of")))
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
    """`sop export` of a release's files, cached for good (releases are immutable; the key has its creation time, so
    a database made again from scratch, as tests do, doesn't get another one's release)."""
    rel = db.get(Release, (ws, number))
    if rel is None:
        return None
    key = (ws, number, rel.created_at.isoformat())
    with _lock:
        if key in _cache:
            return _cache[key]
    files = {}
    for path, version in rel.files.items():
        content = db.exec(select(FileVersion.content).where(FileVersion.workspace == ws, FileVersion.path == path, FileVersion.version == version)).first()
        if content is not None:
            files[path] = content
    try:
        export = store.sop.export(files)
    except Invalid:  # a release in an older sop format
        export = store.sop.export(store.sop.migrate(files)["files"])
    out = {"export": export, "versions": dict(rel.files)}
    with _lock:
        _cache[key] = out
    return out
