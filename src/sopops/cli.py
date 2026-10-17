"""sopops [serve] [--host H] [--port P] [--database-url URL]
sopops import FILE.jsonl --workspace WS [--database-url URL]
sopops worker

Env: DATABASE_URL (default sqlite:///sopops.db), SOPC_BIN for the sopc binary (default: `sopc` on PATH),
HATCHET_CLIENT_TOKEN to run the session pipeline on Hatchet (else it runs inline, in the API process).
Database migrations (Alembic) run on startup. There is no authentication yet: keep it on a private network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pydantic import ValidationError

from .db import database_url

COMMANDS = ("serve", "import", "worker")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in COMMANDS else "serve"
    {"serve": serve, "import": import_sessions, "worker": worker}[command](argv)


def serve(argv: list[str]) -> None:
    import uvicorn

    from .app import create_app
    from .sopc import Sopc
    from .store import Store

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
    from .jobs import Jobs
    from .schemas import SessionIn
    from .sopc import Sopc
    from .store import Store

    parser = argparse.ArgumentParser(prog="sopops import", description="Import sessions from a JSONL file (one canonical session per line). '-' reads stdin.")
    parser.add_argument("file")
    parser.add_argument("--workspace", "-w", required=True)
    parser.add_argument("--database-url", default=database_url())
    args = parser.parse_args(argv)
    from .store import _SAFE_ID

    if not _SAFE_ID.match(args.workspace):
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


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'line'}: {x['msg']}" for x in e.errors()[:3])
    return str(e)


if __name__ == "__main__":
    main()
