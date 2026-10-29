"""The config file is sop.yaml: rename what's stored under its old name, sopc.yaml (from sop v0.0.8 and earlier, then
named sopc, which sopops kept until now), and imports of a sop project are `source` "sop", not "sopc".

Renamed, in every workspace:
- `file_versions.path` (every version, so history and drafts carry on under sop.yaml);
- the key in `releases.files` and the `path` of the config component in `releases.build` (so a release still matches
  head when nothing changed, and its components still point at a file that exists);
- `sync_conflicts.path` (a conflict on the config file);
- the key in `imports.proposal.files` (an imported project applied later writes sop.yaml).
Compiled prompts and their hashes don't depend on the file's name, so no release, serve or session changes.
`repo_connections.pulled_digest` (a hash of the repository's files, names included) is left: the next pull reads the
folder once more and writes nothing, and syncing doesn't compare it. A connected repository that still has sopc.yaml is
read as sop.yaml (`sopops_server.github`), and the next Send to GitHub renames it there.

A workspace that has both files (in any version) is skipped and logged: renaming would collide, and which one to keep
is for its team to decide.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-28
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

OLD, NEW = "sopc.yaml", "sop.yaml"

file_versions = sa.table("file_versions", sa.column("workspace", sa.String), sa.column("path", sa.String))
sync_conflicts = sa.table("sync_conflicts", sa.column("workspace", sa.String), sa.column("path", sa.String))
releases = sa.table("releases", sa.column("workspace", sa.String), sa.column("number", sa.Integer), sa.column("files", sa.JSON),
                    sa.column("build", sa.JSON))
imports = sa.table("imports", sa.column("id", sa.String), sa.column("source", sa.String), sa.column("proposal", sa.JSON))


def renamed(files: dict) -> dict:
    """`files` with OLD's entry as NEW, keeping the order otherwise."""
    return {(NEW if p == OLD else p): v for p, v in files.items()}


def upgrade() -> None:
    db = op.get_bind()
    both = set(db.execute(
        sa.select(file_versions.c.workspace).where(file_versions.c.path.in_([OLD, NEW]))
        .group_by(file_versions.c.workspace).having(sa.func.count(sa.distinct(file_versions.c.path)) == 2)
    ).scalars())
    for ws in sorted(both):
        log.warning("workspace %s has both %s and %s; left as it is: keep one and delete the other", ws, NEW, OLD)
    skip = (lambda col: col.not_in(sorted(both))) if both else (lambda col: sa.true())

    for table in (file_versions, sync_conflicts):
        n = db.execute(sa.update(table).where(table.c.path == OLD, skip(table.c.workspace)).values(path=NEW)).rowcount
        log.info("%s: renamed %s to %s in %d row(s)", table.name, OLD, NEW, n)

    rows = db.execute(sa.select(releases).where(sa.cast(releases.c.files, sa.Text).like(f"%{OLD}%"), skip(releases.c.workspace))).all()
    n = 0
    for r in rows:
        if OLD not in r.files or NEW in r.files:
            continue
        build = dict(r.build)
        build["agents"] = {
            agent: {**a, "components": [({**c, "path": NEW} if c.get("path") == OLD else c) for c in a.get("components", [])]}
            for agent, a in (r.build.get("agents") or {}).items()
        }
        db.execute(sa.update(releases).where(releases.c.workspace == r.workspace, releases.c.number == r.number)
                   .values(files=renamed(r.files), build=build))
        n += 1
    log.info("releases: renamed %s to %s in %d release(s)", OLD, NEW, n)

    rows = db.execute(sa.select(imports.c.id, imports.c.proposal).where(sa.cast(imports.c.proposal, sa.Text).like(f"%{OLD}%"))).all()
    for r in rows:
        files = (r.proposal or {}).get("files") or {}
        if OLD in files and NEW not in files:
            db.execute(sa.update(imports).where(imports.c.id == r.id).values(proposal={**r.proposal, "files": renamed(files)}))
    db.execute(sa.update(imports).where(imports.c.source == "sopc").values(source="sop"))


def downgrade() -> None:
    # sopops before this reads sop.yaml too, so the files stay renamed; only the import source goes back.
    op.get_bind().execute(sa.update(imports).where(imports.c.source == "sop").values(source="sopc"))
