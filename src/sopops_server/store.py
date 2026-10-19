"""Versioned files, releases and the fetch log, in Postgres or SQLite (SQLModel tables in `models`).

Every file is versioned on its own. A release is an immutable {path: version} snapshot plus its compiled build;
the workspace's current release is what agents get. Writes compile the resulting files first, so head always compiles.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Iterator

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, and_, func, select, update

from . import forms
from .auth import actor
from .calibrate import Calibrate
from .checks import Checks
from .db import engine_from_url, upgrade
from .errors import Conflict, NotFound, workspace_id
from .insights import Insights
from .lineage import Lineage, record
from .orm import Fetch, FileVersion, Release, Workspace
from .patterns import Patterns, rekey
from .sessions import Sessions
from .sopc import Files, Invalid, Issue, Sopc, path_issues
from .suggestions import Suggestions
from .tokens import Tokens
from .usage import Usage
from .verdicts import Verdicts

# Which folder each lock.json block kind lives in.
_KIND_DIRS = {"agent": "agents", "instruction": "instructions", "sop": "procedures", "base": "bases"}  # base: sopc v0.0.8
CONFIG = "sopc.yaml"


class Store:
    def __init__(self, engine: Engine | str, sopc: Sopc | None = None, migrate: bool = True):
        """`migrate`: run the Alembic migrations up to head first (see `db.upgrade`)."""
        self.engine = engine_from_url(engine) if isinstance(engine, str) else engine
        self.sopc = sopc or Sopc()
        self.sessions = Sessions(self)
        self.tokens = Tokens(self)
        self.checks = Checks(self)
        self.patterns = Patterns(self)
        self.lineage = Lineage(self, follow=rekey)
        self.verdicts = Verdicts(self)
        self.suggestions = Suggestions(self)
        self.calibrate = Calibrate(self)
        self.insights = Insights(self)
        self.usage = Usage(self)
        if migrate:
            upgrade(self.engine, self.sopc)

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # --- workspaces & files ----------------------------------------------------

    def workspaces(self, org_id: str | None = None) -> list[dict]:
        """Every workspace, or the org's."""
        q = select(Workspace).order_by(Workspace.workspace)
        if org_id is not None:
            q = q.where(Workspace.org_id == org_id)
        with self._session() as db:
            return [w.model_dump() for w in db.exec(q)]

    def head(self, workspace: str, content: bool = False) -> list[dict]:
        """The latest version of every file that isn't deleted."""
        with self._session() as db:
            return [_file_out(r, content) for r in self._head_rows(db, workspace_id(workspace))]

    def file(self, workspace: str, path: str, version: int | None = None) -> dict:
        q = select(FileVersion).where(FileVersion.workspace == workspace_id(workspace), FileVersion.path == path)
        q = q.where(FileVersion.version == version) if version else q.order_by(FileVersion.version.desc()).limit(1)
        with self._session() as db:
            found = db.exec(q).first()
        row = found.model_dump() if found else None
        if not row or (version is None and row["content"] is None):
            raise NotFound(f"file '{path}' not found" + (f" at version {version}" if version else ""))
        return _file_out(row, content=True)

    def history(self, workspace: str, path: str) -> list[dict]:
        q = (
            select(FileVersion)
            .where(FileVersion.workspace == workspace_id(workspace), FileVersion.path == path)
            .order_by(FileVersion.version.desc())
        )
        with self._session() as db:
            rows = [_file_out(r.model_dump()) for r in db.exec(q)]
        if not rows:
            raise NotFound(f"file '{path}' not found")
        return rows

    def edit(self, workspace: str, changes: dict[str, str | None], author: str | None = None, note: str | None = None) -> list[dict]:
        """Apply {path: content, or None to delete} atomically. Raises Invalid if the result doesn't compile."""
        _check_paths(changes)
        with self._locked(workspace) as db:
            return self._write(db, workspace, changes, author, note, replace=False)

    def replace(self, workspace: str, files: Files, author: str | None = None, note: str | None = None) -> list[dict]:
        """Make head exactly `files`; paths not in it are deleted."""
        _check_paths(files)
        with self._locked(workspace) as db:
            return self._write(db, workspace, files, author, note, replace=True)

    # --- items: agents, instructions, procedures, groups and settings as structured data ----

    def items(self, workspace: str) -> dict:
        """Head as {settings, agents, instructions, procedures, groups}, each with its version.
        Instructions, procedures and groups also get `used_by`: [{agent, via}] (via: the group, if not listed directly)."""
        ws = workspace_id(workspace)
        with self._session() as db:
            head = self._head_rows(db, ws)
        return self._items(head)

    def _items(self, head: list[dict]) -> dict:
        files = {r["path"]: r["content"] for r in head}
        if forms.CONFIG in files:
            try:
                items = forms.from_export(self.sopc.export(files))
            except Invalid as e:
                raise forms.located(e) from None
        else:  # a new workspace: sopc.yaml is written with the first item
            items = {"settings": {"variables": {}, "procedures_heading": forms.DEFAULT_HEADING}, "agents": [], "instructions": [], "procedures": [], "groups": []}
        rows = {r["path"]: r for r in head}
        for item in [items["settings"], *items["agents"], *items["instructions"], *items["procedures"], *items["groups"]]:
            row = rows.get(item.get("file", forms.CONFIG))
            item["version"] = row["version"] if row else None
            item["updated_at"] = _utc(row["created_at"]) if row else None
            item["updated_by"] = row["author"] if row else None
        items["settings"]["file"] = forms.CONFIG
        uses = forms.used_by(items)
        for key in ("instructions", "procedures", "groups"):
            for item in items[key]:
                item["used_by"] = uses.get(item["id"], [])
        return items

    def item(self, workspace: str, kind: str, id: str) -> dict:
        found = forms.find(self.items(workspace), kind, id)
        if found is None:
            raise NotFound(f"{_KIND_NAMES[kind]} '{id}' not found")
        return found

    def put_item(self, workspace: str, kind: str, id: str, data: dict, author=None, note=None, create=False) -> tuple[dict, list[dict]]:
        """Create or replace one item from its fields. Returns (the item as saved, files written)."""
        ws = workspace_id(workspace)
        issues = forms.check(kind, id, data)
        if issues:
            raise Invalid(issues)
        with self._locked(ws) as db:
            head = self._head_rows(db, ws)
            items = self._items(head)
            current = forms.find(items, kind, id)
            if kind != "settings" and current is not None and create:
                raise Conflict(f"{_KIND_NAMES[kind]} '{id}' already exists")
            return self._put(db, ws, head, items, kind, id, data, author, note)

    def _put(self, db: Session, ws: str, head: list[dict], items: dict, kind: str, id: str, data: dict, author, note) -> tuple[dict, list[dict]]:
        current = forms.find(items, kind, id)
        other = forms.block_kind(items, id) if kind in forms.BLOCK_KINDS else None
        if other and other != kind:
            raise Invalid([_name_taken(kind, id, other)])
        paths = {r["path"] for r in head}
        if kind in ("settings", "group"):
            config = forms.config_of(items)
            if kind == "settings":
                want = {**config, "variables": data["variables"], "procedures_heading": data["procedures_heading"]}
            else:
                want = {**config, "groups": {**config["groups"], id: list(data["blocks"])}}
            current_path = forms.CONFIG if forms.CONFIG in paths else None
            path, text, saved = forms.write("config", "config", want, current_path, self.sopc.export)
            if current_path and current is not None and forms.same("config", config, saved):
                return current, []
            changes: dict[str, str | None] = {path: text}
        else:
            current_path = current["file"] if current and current.get("version") else None
            path, text, saved = forms.write(kind, id, data, current_path, self.sopc.export)
            if current_path and forms.same(kind, current, saved):
                return current, []
            changes = {path: text}
            if current_path and current_path != path:
                changes[current_path] = None
            if forms.CONFIG not in paths:
                changes[forms.CONFIG] = "version: 1\n"
        written = self._write(db, ws, changes, author, note, replace=False)
        return forms.find(self._items(self._head_rows(db, ws)), kind, id), written

    def delete_item(self, workspace: str, kind: str, id: str, author=None, note=None) -> list[dict]:
        """Delete an item. A shared instruction or procedure is taken out of every agent and group that lists it;
        a group is replaced by its blocks wherever it's listed, so no prompt changes."""
        ws = workspace_id(workspace)
        if kind == "settings":
            raise Conflict("settings can't be deleted")
        with self._locked(ws) as db:
            head = self._head_rows(db, ws)
            items = self._items(head)
            target = forms.find(items, kind, id)
            if target is None:
                raise NotFound(f"{_KIND_NAMES[kind]} '{id}' not found")
            changes: dict[str, str | None] = {}
            config = forms.config_of(items)
            groups = dict(config["groups"])
            if kind == "group":
                del groups[id]
            if kind != "agent":
                replace = target["blocks"] if kind == "group" else []

                def swap(blocks: list[str]) -> list[str]:
                    out: list[str] = []
                    for b in blocks:
                        out += replace if b == id else [b]
                    return out

                for agent in items["agents"]:
                    if id in agent["blocks"]:
                        path, text, _ = forms.write("agent", agent["id"], {**agent, "blocks": swap(agent["blocks"])}, agent["file"], self.sopc.export)
                        changes[path] = text
                groups = {g: swap(blocks) for g, blocks in groups.items()}
            if kind != "group":
                changes[target["file"]] = None
            if groups != config["groups"]:
                path, text, _ = forms.write("config", "config", {**config, "groups": groups}, forms.CONFIG, self.sopc.export)
                changes[path] = text
            return self._write(db, ws, changes, author, note, replace=False)

    def add_block(self, workspace: str, agent: str, block: str, author=None, note=None) -> tuple[dict, list[dict]]:
        """Add a shared instruction, procedure or group to the end of an agent's blocks."""
        ws = workspace_id(workspace)
        with self._locked(ws) as db:
            head = self._head_rows(db, ws)
            items = self._items(head)
            target = forms.find(items, "agent", agent)
            if target is None:
                raise NotFound(f"agent '{agent}' not found")
            if forms.block_kind(items, block) is None:
                raise NotFound(f"there's no shared instruction, procedure or group called '{block}'")
            name = _block_names(items).get(block, block)
            if block in target["blocks"]:
                raise Conflict(f"{agent} already lists {name}")
            via = [v for b, v in forms.expand(items, target["blocks"]) if b == block]
            if via:
                raise Conflict(f"{agent} already gets {name} through the group {via[0]}")
            return self._put(db, ws, head, items, "agent", agent, {**target, "blocks": [*target["blocks"], block]}, author, note)

    def remove_block(self, workspace: str, agent: str, block: str, author=None, note=None) -> tuple[dict, list[dict]]:
        """Take a block out of an agent's blocks. One the agent gets through a group can't be removed here."""
        ws = workspace_id(workspace)
        with self._locked(ws) as db:
            head = self._head_rows(db, ws)
            items = self._items(head)
            target = forms.find(items, "agent", agent)
            if target is None:
                raise NotFound(f"agent '{agent}' not found")
            name = _block_names(items).get(block, block)
            if block not in target["blocks"]:
                via = [v for b, v in forms.expand(items, target["blocks"]) if b == block]
                if via:
                    raise Conflict(f"{agent} gets {name} through the group {via[0]}; take it out of that group, or list the group's other blocks instead of the group")
                raise NotFound(f"{agent} doesn't use {name}")
            blocks = [b for b in target["blocks"] if b != block]
            return self._put(db, ws, head, items, "agent", agent, {**target, "blocks": blocks}, author, note)

    def preview_agent(self, workspace: str, agent: str, data: dict) -> dict:
        """Compile head with this agent's unsaved fields, without saving anything."""
        ws = workspace_id(workspace)
        with self._session() as db:
            head = self._head_rows(db, ws)
            current = self._current(db, ws)
        out = {"valid": False, "issues": [], "prompt": None, "hash": None, "blocks": [], "released_prompt": None, "release": current["number"] if current else None}
        if current and agent in current["build"]["agents"]:
            out["released_prompt"] = current["build"]["agents"][agent]["prompt"]
        issues = forms.check("agent", agent, data)
        if issues:
            return {**out, "issues": [i.to_dict() for i in issues]}
        files = {r["path"]: r["content"] for r in head}
        items = self._items(head)
        existing = forms.find(items, "agent", agent)
        try:
            path, text, _ = forms.write("agent", agent, data, existing["file"] if existing and existing.get("version") else None, self.sopc.export)
        except Invalid as e:
            return {**out, "issues": [i.to_dict() for i in forms.located(e).issues]}
        files[path] = text
        files.setdefault(forms.CONFIG, "version: 1\n")
        compiled = self.sopc.compile(files)
        names = _block_names(items)
        out["issues"] = [forms.locate(i, names).to_dict() for i in compiled.issues]
        built = compiled.agents.get(agent) if compiled.valid else None
        if built:
            out.update(valid=True, prompt=built["prompt"], hash=built["hash"], blocks=[{"kind": b["kind"], "id": b["id"]} for b in built["blocks"]])
        return out

    def migrate(self, workspace: str, apply: bool = False, author=None, note=None) -> dict:
        """Convert head from an older sopc format with `sopc migrate` (v0.0.8 targeting, or v0.0.9 `locked`).
        Without `apply`, only the plan."""
        ws = workspace_id(workspace)
        with self._locked(ws) as db:
            files = {r["path"]: r["content"] for r in self._head_rows(db, ws)}
            result = self.sopc.migrate(files) if files else {"migrated": False, "plan": "", "files": {}}
            if not result["migrated"]:
                return {"workspace": ws, "needed": False, "plan": "", "applied": False, "changed": []}
            changed = self._write(db, ws, result["files"], author, note, replace=True) if apply else []
            return {"workspace": ws, "needed": True, "plan": result["plan"], "applied": apply, "changed": changed}

    def _parse(self, kind: str, id: str, path: str, content: str) -> dict | None:
        """One item's fields from one file's content, read by sopc on its own. A version that sets `locked`
        (sopc v0.0.9) is read as `sopc migrate` converts it. None if it's in the v0.0.8 format."""
        if kind in ("settings", "group"):
            items = self._parse_config(content)
            return forms.find(items, kind, id) if items else None
        files = {forms.CONFIG: "", path: content}
        try:
            return forms.find(forms.from_export(self.sopc.export(files)), kind, id)
        except Invalid as e:
            if not all(i.code == "removed_field" for i in e.issues):
                return None
        try:
            return forms.find(forms.from_export(self.sopc.export(self.sopc.migrate(files)["files"])), kind, id)
        except Invalid:
            return None

    def _parse_config(self, content: str) -> dict | None:
        """sopc.yaml's settings and groups, migrating an old-format one."""
        for files in ({forms.CONFIG: content}, None):
            try:
                if files is None:
                    files = self.sopc.migrate({forms.CONFIG: content})["files"]
                return forms.from_export(self.sopc.export(files))
            except Invalid:
                continue
        return None

    def _release_items(self, db: Session, ws: str, versions: dict[str, int]) -> dict | None:
        """The items of a release's files; a release in the old format is read as `sopc migrate` converts it."""
        if not versions:
            return None
        files = {}
        for path, version in versions.items():
            content = db.exec(select(FileVersion.content).where(FileVersion.workspace == ws, FileVersion.path == path, FileVersion.version == version)).first()
            if content is not None:
                files[path] = content
        try:
            return forms.from_export(self.sopc.export(files))
        except Invalid:
            pass
        try:
            return forms.from_export(self.sopc.export(self.sopc.migrate(files)["files"]))
        except Invalid:
            return None

    def item_history(self, workspace: str, kind: str, id: str) -> list[dict]:
        """Every version of an item, newest first. A procedure moved from Markdown to YAML keeps both histories.
        Settings and groups share sopc.yaml, so each lists only the versions that changed it."""
        ws = workspace_id(workspace)
        q = (
            select(FileVersion)
            .where(FileVersion.workspace == ws, FileVersion.path.in_(forms.paths_of(kind, id)))
            .order_by(FileVersion.created_at.desc(), FileVersion.version.desc())
        )
        with self._session() as db:
            raw = [r.model_dump() for r in db.exec(q)]
        rows = [{**{k: v for k, v in _file_out(r).items() if k != "path"}, "file": r["path"]} for r in raw]
        if kind in ("settings", "group"):
            keep, values = [], []
            for r in raw:
                parsed = self._parse(kind, id, r["path"], r["content"]) if r["content"] is not None else None
                values.append(_fields(kind, parsed) if parsed is not None else None)
            for i, row in enumerate(rows):
                older = values[i + 1] if i + 1 < len(values) else None
                if values[i] != older and not (values[i] is None and kind == "group" and older is None):
                    keep.append({**row, "deleted": values[i] is None})
            rows = keep
        if not rows:
            raise NotFound(f"{_KIND_NAMES[kind]} '{id}' not found")
        return rows

    def item_version(self, workspace: str, kind: str, id: str, version: int, file: str | None = None) -> dict:
        """An older version of an item, as fields. One saved in the old format comes back as `legacy` with its `content`."""
        paths = forms.paths_of(kind, id)
        if file is not None and file not in paths:
            raise NotFound(f"'{file}' is not a file of {_KIND_NAMES[kind]} '{id}'")
        for path in [file] if file else paths:
            try:
                row = self.file(workspace, path, version)
            except NotFound:
                continue
            meta = {"file": path, "version": version, "updated_at": row["created_at"], "updated_by": row["author"]}
            if row["deleted"]:
                return {"id": id, "deleted": True, **meta}
            found = self._parse(kind, id, path, row["content"])
            if found is None and kind == "group":
                return {"id": id, "deleted": True, **meta}
            if found is None:
                return {"id": id, "deleted": False, "legacy": True, "content": row["content"], **meta}
            return {**found, "id": id, "deleted": False, **meta}
        raise NotFound(f"{_KIND_NAMES[kind]} '{id}' has no version {version}")

    # --- releases --------------------------------------------------------------

    def draft(self, workspace: str) -> dict:
        """Head compiled, compared with the current release."""
        ws = workspace_id(workspace)
        with self._session() as db:
            head = self._head_rows(db, ws)
            current = self._current(db, ws)
            live_files = current["files"] if current else {}
            before_items = self._release_items(db, ws, live_files)
        live_agents = current["build"]["agents"] if current else {}
        head_versions = {r["path"]: r["version"] for r in head}

        files = []
        for path in sorted(set(head_versions) | set(live_files)):
            new, old = head_versions.get(path), live_files.get(path)
            if new != old:
                change = "added" if old is None else "deleted" if new is None else "edited"
                files.append({"path": path, "change": change, "version": new, "released_version": old})

        head_files = {r["path"]: r["content"] for r in head}
        compiled = self.sopc.compile(head_files) if head else None
        lint = []
        if compiled and compiled.valid:
            try:
                lint = self.sopc.lint(head_files)
            except Invalid:
                pass
        try:
            after_items = self._items(head) if head else None
        except Invalid:
            after_items = None
        names = {**_block_names(before_items), **_block_names(after_items)}

        items: dict[tuple[str, str], dict] = {}
        for f in files:
            where = forms.item_of_path(f["path"])
            if not where or where[0] == "settings":
                continue
            prev = items.get(where)
            change = f["change"]
            if prev:  # a procedure moved between Markdown and YAML, or a base became an instruction: one edit
                change = "edited" if {prev["change"], change} == {"added", "deleted"} else prev["change"]
            items[where] = {
                "kind": where[0],
                "id": where[1],
                "change": change,
                "version": f["version"] if f["version"] is not None else (prev or {}).get("version"),
                "released_version": f["released_version"] if f["released_version"] is not None else (prev or {}).get("released_version"),
            }
        # sopc.yaml holds the settings and every group: one entry for each that changed.
        config_file = next((f for f in files if f["path"] == forms.CONFIG), None)
        if config_file:
            for kind, id in [("settings", "settings")] + [("group", g) for g in _ordered_union(before_items, after_items, "groups")]:
                b = forms.find(before_items, kind, id) if before_items else None
                a = forms.find(after_items, kind, id) if after_items else None
                if b is not None and a is not None and _fields(kind, b) == _fields(kind, a):
                    continue
                if b is None and a is None:
                    continue
                items[(kind, id)] = {
                    "kind": kind,
                    "id": id,
                    "change": "added" if b is None else "deleted" if a is None else "edited",
                    "version": config_file["version"],
                    "released_version": config_file["released_version"],
                }
        # Each changed item's fields before (as released) and after (head), for a field-by-field summary.
        for (kind, id), item in items.items():
            item["name"] = "Settings" if kind == "settings" else names.get(id, id) if kind != "agent" else id
            after = forms.find(after_items, kind, id) if after_items else None
            before = forms.find(before_items, kind, id) if before_items else None
            item["after"] = _fields(kind, after) if after is not None and item["change"] != "deleted" else None
            item["before"] = _fields(kind, before) if before is not None and item["change"] != "added" else None
        draft_agents = compiled.agents if compiled and compiled.valid else {}
        agents = []
        for agent_id in sorted(set(draft_agents) | set(live_agents)):
            new, old = draft_agents.get(agent_id), live_agents.get(agent_id)
            status = "added" if old is None else "removed" if new is None else "unchanged" if new["hash"] == old["hash"] else "changed"
            agents.append(
                {
                    "agent": agent_id,
                    "platform_ref": (new or old)["platform_ref"],
                    "status": status,
                    "hash": new["hash"] if new else None,
                    "released_hash": old["hash"] if old else None,
                    "prompt": new["prompt"] if new else None,
                    "released_prompt": old["prompt"] if old else None,
                    "components": _components(new["blocks"], head_versions) if new else [],
                }
            )
        return {
            "workspace": ws,
            "release": current["number"] if current else None,
            "valid": compiled.valid if compiled else False,
            "issues": [forms.locate(i, names).to_dict() for i in compiled.issues] if compiled else [],
            "files": files,
            "items": list(items.values()),
            "agents": agents,
            "lint": lint,
        }

    def release(self, workspace: str, author: str | None = None, note: str | None = None) -> tuple[dict, bool]:
        """Release head and make it current. If head is already current, returns that release and False."""
        with self._locked(workspace) as db:
            return self._release(db, workspace, author, note)

    def publish(self, workspace: str, files: Files, author: str | None = None, note: str | None = None) -> tuple[dict, bool, list[dict]]:
        """Replace head with `files` and release it, in one transaction."""
        _check_paths(files)
        with self._locked(workspace) as db:
            changed = self._write(db, workspace, files, author, note, replace=True)
            release, created = self._release(db, workspace, author, note)
            return release, created, changed

    def releases(self, workspace: str) -> list[dict]:
        ws = workspace_id(workspace)
        q = select(Release.number, Release.created_at, Release.author, Release.note, Release.files)
        with self._session() as db:
            current = self._current_number(db, ws)
            rows = db.exec(q.where(Release.workspace == ws).order_by(Release.number.desc())).mappings()
            return [_summary(r, r["number"] == current) for r in rows]

    def get_release(self, workspace: str, number: int | None = None) -> dict:
        """A release with its build; the current one if `number` is None."""
        ws = workspace_id(workspace)
        with self._session() as db:
            current = self._current_number(db, ws)
            number = number or current
            if number is None:
                raise NotFound(f"workspace '{ws}' has no release")
            found = db.get(Release, (ws, number))
        row = found.model_dump() if found else None
        if not row:
            raise NotFound(f"release {number} not found")
        return {**_summary(row, row["number"] == current), "build": row["build"]}

    def activate(self, workspace: str, number: int) -> dict:
        """Serve an existing release, e.g. to roll back."""
        ws = workspace_id(workspace)
        with self._session() as db, db.begin():
            if db.get(Release, (ws, number)) is None:
                raise NotFound(f"release {number} not found")
            db.execute(update(Workspace).where(Workspace.workspace == ws).values(current_release=number))
        return {"workspace": ws, "current_release": number}

    # --- serving ---------------------------------------------------------------

    def agent(self, workspace: str, agent: str, release: int | None = None) -> dict:
        """The agent's build in a release (default: current). `agent` is the sopc id or platform ref."""
        rel = self.get_release(workspace, release)
        built = rel["build"]["agents"]
        agent_id = agent if agent in built else next((a for a, b in built.items() if b["platform_ref"] == agent), None)
        if agent_id is None:
            raise NotFound(f"agent '{agent}' not found in release {rel['number']}")
        return {"agent": agent_id, "release": rel["number"], **built[agent_id]}

    def log_fetch(self, workspace: str, agent: str, release: int, hash: str) -> None:
        with self._session() as db, db.begin():
            db.add(Fetch(workspace=workspace, agent=agent, release=release, hash=hash, at=_now()))

    def fetches(self, workspace: str, agent: str | None = None, limit: int = 100) -> list[dict]:
        """Newest first."""
        q = select(Fetch).where(Fetch.workspace == workspace_id(workspace))
        if agent:
            q = q.where(Fetch.agent == agent)
        with self._session() as db:
            rows = db.exec(q.order_by(Fetch.id.desc()).limit(limit))
            return [{"id": f.id, "agent": f.agent, "release": f.release, "hash": f.hash, "at": _utc(f.at)} for f in rows]

    # --- internals -------------------------------------------------------------

    @contextmanager
    def _locked(self, workspace: str) -> Iterator[Session]:
        """A transaction holding the workspace's lock, so file versions and release numbers don't race."""
        ws = workspace_id(workspace)
        try:
            with self._session() as db, db.begin():
                name = db.get_bind().dialect.name
                dialect = postgresql if name == "postgresql" else sqlite
                if db.get(Workspace, ws) is None:
                    db.execute(dialect.insert(Workspace.__table__).values(workspace=ws, org_id=actor.workspace_org(db)).on_conflict_do_nothing())
                if name == "postgresql":
                    db.exec(select(Workspace.workspace).where(Workspace.workspace == ws).with_for_update())
                yield db
        except IntegrityError as e:
            raise Conflict("a concurrent write conflicted; retry") from e

    def _write(self, db: Session, workspace: str, changes: dict[str, str | None], author, note, replace: bool) -> list[dict]:
        head = {r["path"]: r for r in self._head_rows(db, workspace)}
        current = {p: r["content"] for p, r in head.items()}
        target = {p: c for p, c in (changes.items() if replace else {**current, **changes}.items()) if c is not None}
        if target == current:
            return []
        compiled = self.sopc.compile(target)
        if not compiled.valid:
            raise Invalid([forms.locate(i, self._names(target)) for i in compiled.issues])
        now, written = _now(), []
        for path in sorted(set(target) | set(current)):
            new = target.get(path)
            if new == current.get(path):
                continue
            version = self._max_version(db, workspace, path) + 1
            db.add(FileVersion(workspace=workspace, path=path, version=version, content=new, created_at=now, author=author, note=note))
            written.append({"path": path, "version": version, "deleted": new is None})
        return written

    def _names(self, files: Files) -> dict[str, str]:
        try:
            return _block_names(forms.from_export(self.sopc.export(files)))
        except Invalid:
            return {}

    def _release(self, db: Session, workspace: str, author, note) -> tuple[dict, bool]:
        head = self._head_rows(db, workspace)
        if not head:
            raise Invalid([Issue("missing_config", "the workspace has no files", "", "error")])
        versions = {r["path"]: r["version"] for r in head}
        current = self._current(db, workspace)
        if current and current["files"] == versions:
            return _summary(current, True), False
        files = {r["path"]: r["content"] for r in head}
        compiled = self.sopc.compile(files)
        if not compiled.valid:
            raise Invalid([forms.locate(i, self._names(files)) for i in compiled.issues])
        build = {
            "agents": {
                agent_id: {
                    "platform_ref": a["platform_ref"],
                    "hash": a["hash"],
                    "prompt": a["prompt"],
                    "tools": a["tools"],
                    "sops": a["sops"],
                    "components": _components(a["blocks"], versions),
                }
                for agent_id, a in compiled.agents.items()
            }
        }
        number = (db.exec(select(func.max(Release.number)).where(Release.workspace == workspace)).one() or 0) + 1
        row = {"workspace": workspace, "number": number, "created_at": _now(), "author": author, "note": note, "files": versions, "build": build}
        rel = Release(**row)
        db.add(rel)
        db.execute(update(Workspace).where(Workspace.workspace == workspace).values(current_release=number))
        db.flush()
        row["author"] = rel.author  # signed in: the user's name (sopops_server.auth.actor)
        # which rules the new release rewords, against the release that was live (patterns follow; sopops_server.lineage)
        record(db, self.sopc, workspace, current["number"] if current else None, number, follow=rekey)
        return _summary(row, True), True

    def _head_rows(self, db: Session, workspace: str) -> list[dict]:
        latest = (
            select(FileVersion.path, func.max(FileVersion.version).label("version"))
            .where(FileVersion.workspace == workspace)
            .group_by(FileVersion.path)
            .subquery()
        )
        q = (
            select(FileVersion)
            .join(latest, and_(FileVersion.path == latest.c.path, FileVersion.version == latest.c.version))
            .where(FileVersion.workspace == workspace, FileVersion.content.is_not(None))
            .order_by(FileVersion.path)
        )
        return [r.model_dump() for r in db.exec(q)]

    def _max_version(self, db: Session, workspace: str, path: str) -> int:
        q = select(func.max(FileVersion.version)).where(FileVersion.workspace == workspace, FileVersion.path == path)
        return db.exec(q).one() or 0

    def _current_number(self, db: Session, workspace: str) -> int | None:
        return db.exec(select(Workspace.current_release).where(Workspace.workspace == workspace)).first()

    def _current(self, db: Session, workspace: str) -> dict | None:
        number = self._current_number(db, workspace)
        if number is None:
            return None
        return db.get_one(Release, (workspace, number)).model_dump()


def _components(blocks: list[dict], versions: dict[str, int]) -> list[dict]:
    """Map lock.json blocks to the file versions they came from, plus sopc.yaml (defaults, headings)."""
    out = []
    for block in blocks:
        folder = _KIND_DIRS[block["kind"]]
        path = next((p for p in versions if p.startswith(folder + "/") and p.count("/") == 1 and PurePosixPath(p).stem == block["id"]), None)
        out.append({"kind": block["kind"], "id": block["id"], "path": path, "version": versions.get(path)})
    if CONFIG in versions:
        out.append({"kind": "config", "id": "sopc", "path": CONFIG, "version": versions[CONFIG]})
    return out


_KIND_NAMES = {"agent": "agent", "instruction": "shared instruction", "procedure": "procedure", "group": "group", "settings": "settings"}


def _name_taken(kind: str, id: str, other: str) -> Issue:
    text = f"'{id}' is already the name of a {_KIND_NAMES[other]}; pick another name."
    return Issue("duplicate_id", text, "", "error", kind=kind, id=id, field="id", text=text)


def _fields(kind: str, item: dict) -> dict:
    """Just what a form edits (no versions, file paths or used_by)."""
    return {"id": item.get("id", "settings"), **{f: item[f] for f in forms.FIELDS[kind]}}


def _block_names(items: dict | None) -> dict[str, str]:
    """What people call each block: a procedure's name, else its id."""
    return {p["id"]: p["name"] for p in items["procedures"]} if items else {}


def _ordered_union(a: dict | None, b: dict | None, key: str) -> list[str]:
    out: list[str] = []
    for items in (b, a):
        for x in (items or {}).get(key, []):
            if x["id"] not in out:
                out.append(x["id"])
    return out


def _check_paths(files: dict) -> None:
    bad = path_issues(files)
    if bad:
        raise Invalid(bad)


def _summary(row, current: bool) -> dict:
    return {
        "number": row["number"],
        "created_at": _utc(row["created_at"]),
        "author": row["author"],
        "note": row["note"],
        "files": row["files"],
        "current": current,
    }


def _file_out(row, content: bool = False) -> dict:
    out = {
        "path": row["path"],
        "version": row["version"],
        "deleted": row["content"] is None,
        "created_at": _utc(row["created_at"]),
        "author": row["author"],
        "note": row["note"],
    }
    if content:
        out["content"] = row["content"]
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    """Everything is stored in UTC. SQLite drops the timezone; Postgres answers in its session's time zone."""
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
