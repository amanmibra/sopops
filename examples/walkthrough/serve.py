"""Start sopops for the walkthrough: its own database, on 127.0.0.1 only, in fake or real model mode.

    python examples/walkthrough/serve.py                 # fake mode, out/walkthrough.db, port 8585
    python examples/walkthrough/serve.py --models real   # your models, from the usual variables (see WALKTHROUGH.md)
    python examples/walkthrough/serve.py --fresh         # start from an empty database

Fake mode answers every model call with `fake_models.WalkthroughModels` (no network, no keys, no cost). Real mode is
plain `sopops`: SOPOPS_MODEL_FAST / SOPOPS_MODEL_REASONING (and TYPESAFE_API_KEY for Jev) from the environment.
`WALKTHROUGH_DATABASE_URL` overrides the database (e.g. a throwaway Postgres); `DATABASE_URL` is ignored, so a
sopops you already run keeps its database.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import uvicorn

from common import OUT, PORT
from fake_models import WalkthroughModels
from sopops_server import Sopc, Store, create_app
from sopops_server.ai.lm import configured, set_ai
from sopops_server.jobs import Jobs


SOPC_MIN = (0, 0, 17)


def sopc_version(binary: str) -> tuple[int, ...]:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", choices=["fake", "real"], default=os.environ.get("WALKTHROUGH_MODELS", "fake"))
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--database-url", default=os.environ.get("WALKTHROUGH_DATABASE_URL") or f"sqlite:///{OUT / 'walkthrough.db'}")
    parser.add_argument("--fresh", action="store_true", help="Delete out/ first: the SQLite database, generated calls and tokens (a Postgres database isn't touched).")
    args = parser.parse_args()

    if args.fresh:
        for f in OUT.glob("*"):
            if f.is_file():
                f.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.database_url.startswith("sqlite:///"):
        Path(args.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    sopc = Sopc()
    if not sopc.binary:
        sys.exit(f"sopc not found: install v{'.'.join(map(str, SOPC_MIN))} or later, or set SOPC_BIN")
    found = sopc_version(sopc.binary)
    if found < SOPC_MIN:
        sys.exit(
            f"{sopc.binary} is sopc v{'.'.join(map(str, found))}; the walkthrough needs v{'.'.join(map(str, SOPC_MIN))} or later.\n"
            "Update it (curl -fsSL https://raw.githubusercontent.com/amanmibra/sopc/main/install.sh | sh) or point SOPC_BIN at a newer one."
        )
    store = Store(args.database_url, sopc)
    if args.models == "fake":
        os.environ["SOPOPS_CHECK_MODE"] = "review"  # the scripted reviewer judges every rule
        ai = WalkthroughModels()
        set_ai(ai)
        app = create_app(store, Jobs(store, use_hatchet=False, ai=ai))
    else:
        if not configured():
            sys.exit("real mode needs SOPOPS_MODEL_FAST and SOPOPS_MODEL_REASONING (and the provider's key); see WALKTHROUGH.md")
        app = create_app(store)
    print(f"sopops ({args.models} models) on http://127.0.0.1:{args.port}, database {args.database_url}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
