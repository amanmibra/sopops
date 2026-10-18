"""sopops [serve] [--host H] [--port P] [--database-url URL]
sopops import FILE.jsonl --workspace WS [--database-url URL]
sopops worker
sopops recluster [--workspace WS] [--procedure P] [--rule R] [--force] [--database-url URL]
sopops calibrate --workspace WS [--procedure P] [--dry-run] [--database-url URL]
sopops pr-status [--workspace WS] [--database-url URL]
sopops lineage rebuild [--workspace WS] [--database-url URL]
sopops patterns rename [--workspace WS] [--all] [--database-url URL]

Env: DATABASE_URL (default sqlite:///sopops.db), SOPC_BIN for the sopc binary (default: `sopc` on PATH),
HATCHET_CLIENT_TOKEN to run the session pipeline on Hatchet (else it runs inline, in the API process).
Database migrations (Alembic) run on startup. There is no authentication yet: keep it on a private network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import uvicorn
from pydantic import ValidationError

from . import repo_sync
from .app import create_app
from .db import database_url
from .errors import SAFE_ID
from .jobs import Jobs
from .onboarding import pulls
from .schemas import SessionIn
from .sopc import Sopc
from .store import Store

COMMANDS = ("serve", "import", "worker", "recluster", "calibrate", "pr-status", "lineage", "patterns")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in COMMANDS else "serve"
    {"serve": serve, "import": import_sessions, "worker": worker, "recluster": recluster, "calibrate": calibrate, "pr-status": pr_status,
     "lineage": lineage, "patterns": patterns}[command](argv)


def serve(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="sopops", description="Versioned sopc workspaces, each agent's prompt at session start, and sessions.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8484)))
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    sopc = Sopc()
    if not sopc.binary:
        parser.error("sopc not found; install it or set SOPC_BIN")
    uvicorn.run(create_app(Store(args.database_url, sopc)), host=args.host, port=args.port)


def import_sessions(argv: list[str]) -> None:
    """Read canonical session JSON, one per line (see API.md), into a workspace. Re-importing a file is safe."""
    parser = argparse.ArgumentParser(prog="sopops import", description="Import sessions from a JSONL file (one canonical session per line). '-' reads stdin.")
    parser.add_argument("file")
    parser.add_argument("--workspace", "-w", required=True)
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    if not SAFE_ID.match(args.workspace):
        parser.error(f"invalid workspace id '{args.workspace}'")

    store = Store(args.database_url, Sopc())
    jobs = Jobs(store)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}
    stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    with stream:
        for n, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                payload = SessionIn.model_validate(json.loads(line))
                done = store.sessions.ingest(args.workspace, payload)
            except (ValueError, ValidationError) as e:
                counts["failed"] += 1
                print(f"line {n}: {_short(e)}", file=sys.stderr)
                continue
            for w in done.warnings:
                print(f"line {n}: {w}", file=sys.stderr)
            if done.changed:
                jobs.process_session(done.ref)
            counts["created" if done.created else "updated" if done.updated else "unchanged"] += 1
    print(", ".join(f"{v} {k}" for k, v in counts.items()) + f" (pipeline: {jobs.mode})")
    if counts["failed"]:
        sys.exit(1)


def worker(argv: list[str]) -> None:
    """Run the Hatchet worker for the session pipeline."""
    argparse.ArgumentParser(prog="sopops worker", description="Run the Hatchet worker (needs HATCHET_CLIENT_TOKEN and sopops[hatchet]).").parse_args(argv)
    if not os.environ.get("HATCHET_CLIENT_TOKEN"):
        sys.exit("sopops worker: set HATCHET_CLIENT_TOKEN (without Hatchet, the API runs the pipeline itself)")
    try:
        from .jobs.hatchet import start_worker
    except ImportError:
        sys.exit("sopops worker: install the Hatchet SDK: pip install 'sopops[hatchet]'")
    start_worker()


def recluster(argv: list[str]) -> None:
    """Assign unassigned findings to patterns, recluster rules and regroup themes (what the daily cron runs)."""
    parser = argparse.ArgumentParser(prog="sopops recluster", description="Assign findings to patterns, recluster rules whose triggers fired (every rule with --force), and regroup themes. Calls the models.")
    parser.add_argument("--workspace", "-w", help="Only this workspace (default: all).")
    parser.add_argument("--procedure", help="Only this procedure's rules (with --force).")
    parser.add_argument("--rule", help="Only this rule (with --procedure).")
    parser.add_argument("--force", action="store_true", help="Recluster even where no trigger fired (rules still need 8 findings).")
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    if args.rule and not args.procedure:
        parser.error("--rule needs --procedure")
    store = Store(args.database_url, Sopc())
    pats = store.patterns
    out = pats.assign_pending(args.workspace)
    if args.rule:
        workspaces = [args.workspace] if args.workspace else [w["workspace"] for w in store.workspaces()]
        for ws in workspaces:
            if pats.recluster(ws, args.procedure, args.rule, force=args.force):
                out.reclustered.append(args.rule)
                if pats.regroup_themes(ws, args.procedure, force=True):
                    out.themed.append(args.procedure)
    elif args.force:
        workspaces = [args.workspace] if args.workspace else [w["workspace"] for w in store.workspaces()]
        for ws in workspaces:
            more = pats.recluster_all(ws, args.procedure)
            out.reclustered += more.reclustered
            out.themed += more.themed
    else:
        more = pats.recluster_due(args.workspace)
        out.reclustered += more.reclustered
        out.themed += more.themed
    print(f"{out.assigned} findings assigned, {len(out.reclustered)} rules reclustered, {len(set(out.themed))} procedures regrouped into themes")


def calibrate(argv: list[str]) -> None:
    """Fit a workspace's check thresholds to its review labels (see `sopops.calibrate`)."""
    parser = argparse.ArgumentParser(prog="sopops calibrate", description="Fit check thresholds to the workspace's verdicts and overrides. Calls the decision model once per labeled session.")
    parser.add_argument("--workspace", "-w", required=True)
    parser.add_argument("--procedure", help="Only this procedure (default: every procedure with labeled sessions).")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without saving.")
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    if not SAFE_ID.match(args.workspace):
        parser.error(f"invalid workspace id '{args.workspace}'")
    out = Store(args.database_url, Sopc()).calibrate.run(args.workspace, args.procedure, args.dry_run)
    if not out["procedures"]:
        print("no labeled sessions: give patterns verdicts or override findings first")
    for p in out["procedures"]:
        head = f"{p['procedure_id']}: {p['labels']} labeled sessions ({p['negative_labels']} findings judged wrong)"
        if p["skipped"]:
            print(f"{head}; refused: {p['skipped']['message']}")
            continue
        print(f"{head}; objective {p['score_before']} -> {p['score']}{' (dry run, not saved)' if args.dry_run else ''}")
        for r in p["rules"]:
            if r["after"] != r["before"]:
                print(f"  {r['field']} ({r['rule_id'] or 'goal'}): {r['before']} -> {r['after']}")
        if all(r["after"] == r["before"] for r in p["rules"]):
            print("  no threshold changed")


def pr_status(argv: list[str]) -> None:
    """Check open pull requests on GitHub, suggestions', onboarding's and the sync pull request, and sync each repository
    connection that was synced before (what the Hatchet cron runs; without Hatchet, run it from cron)."""
    parser = argparse.ArgumentParser(prog="sopops pr-status", description="Check open pull requests: suggestions' (merged ones become merged, closed ones "
                                     "dismissed) and onboarding's (merged ones are pulled into drafts).")
    parser.add_argument("--workspace", "-w", help="Only this workspace (default: all).")
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    store = Store(args.database_url, Sopc())
    out = store.suggestions.sync_prs(args.workspace, force=True)
    print("suggestions: " + ", ".join(f"{v} {k}" for k, v in out.items()))
    out = pulls.sync(store, args.workspace, force=True)
    print("onboarding: " + ", ".join(f"{v} {k}" for k, v in out.items()))
    out = repo_sync.poll(store, args.workspace, force=True)
    print("repositories: " + ", ".join(f"{v} {k}" for k, v in out.items()))


def lineage(argv: list[str]) -> None:
    """Recompute rule lineage from the releases (what the migration that added it ran; see `sopops.lineage`)."""
    parser = argparse.ArgumentParser(prog="sopops lineage", description="Rule lineage: which rule a reworded rule replaced.")
    parser.add_argument("action", choices=["rebuild"], help="rebuild: match every release's rules against the release before it (manual links stay), and move patterns to match.")
    parser.add_argument("--workspace", "-w", help="Only this workspace (default: all).")
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    store = Store(args.database_url, Sopc())
    for ws in [args.workspace] if args.workspace else [w["workspace"] for w in store.workspaces()]:
        out = store.lineage.rebuild(ws)
        print(f"{ws}: {out['links']} rules linked to an earlier wording, {out['procedures']} procedures")


def patterns(argv: list[str]) -> None:
    """Name rule patterns from their rules (see `sopops.patterns.Patterns.name`): the backfill for patterns named by index."""
    parser = argparse.ArgumentParser(prog="sopops patterns", description="Rule patterns.")
    parser.add_argument("action", choices=["rename"], help="rename: name rule patterns that no model has named yet from their rule's current wording, with the "
                        "reasoning model (SOPOPS_MODEL_REASONING), else the rule's text trimmed.")
    parser.add_argument("--workspace", "-w", help="Only this workspace (default: all).")
    parser.add_argument("--all", action="store_true", help="Every rule pattern, including those a model already named.")
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    store = Store(args.database_url, Sopc())
    for ws in [args.workspace] if args.workspace else [w["workspace"] for w in store.workspaces()]:
        print(f"{ws}: {store.patterns.name(ws, force=args.all)} rule patterns renamed")


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'line'}: {x['msg']}" for x in e.errors()[:3])
    return str(e)


if __name__ == "__main__":
    main()
