"""Start sopops for the walkthrough: its own database, on 127.0.0.1 only, in fake or real model mode.

    python examples/walkthrough/serve.py                 # fake mode, out/walkthrough.db, port 8585
    python examples/walkthrough/serve.py --models real   # your models, from the usual variables (see WALKTHROUGH.md)
    python examples/walkthrough/serve.py --fresh         # start from an empty database
    python examples/walkthrough/serve.py --sign-in       # with sign-in on, and an account to sign in with

Fake mode answers every model call with `fake_models.WalkthroughModels` (no network, no keys, no cost). Real mode is
plain `sopops`: SOPOPS_MODEL_FAST / SOPOPS_MODEL_REASONING (and TYPESAFE_API_KEY for Jev) from the environment.
`--sign-in` turns sign-in on with passwords, opens sign-up to anyone, and makes the account in DEMO_ACCOUNT below
if it isn't there yet, so the UI can be looked at the way a signed-in team sees it. Without it the walkthrough runs
with sign-in off, which is what the steps expect.

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
from sopops_server.auth.service import Accounts
from sopops_server.jobs import Jobs


SOPC_MIN = (0, 0, 17)


def sopc_version(binary: str) -> tuple[int, ...]:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


DEMO_ACCOUNT = {"email": "test@example.com", "name": "Test User", "password": "password123"}


def sign_in_on(store: Store) -> None:
    """Sign-in with passwords, sign-up open to anyone, and the demo account, for looking around the UI.

    The password is eleven characters because sopops asks for ten or more; `password` on its own is refused."""
    os.environ["SOPOPS_AUTH_REQUIRED"] = "1"
    os.environ["SOPOPS_LOGIN_PROVIDERS"] = "password"
    os.environ["SOPOPS_OPEN_SIGNUP"] = "1"
    accounts = Accounts(store)
    if accounts.has_users():
        print(f"sign-in on; an account already exists (the demo one is {DEMO_ACCOUNT['email']})", flush=True)
        return
    accounts.first_account(DEMO_ACCOUNT["email"], DEMO_ACCOUNT["name"], DEMO_ACCOUNT["password"])
    print(f"sign-in on; sign in as {DEMO_ACCOUNT['email']} / {DEMO_ACCOUNT['password']}, or sign up as anyone", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", choices=["fake", "real"], default=os.environ.get("WALKTHROUGH_MODELS", "fake"))
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--database-url", default=os.environ.get("WALKTHROUGH_DATABASE_URL") or f"sqlite:///{OUT / 'walkthrough.db'}")
    parser.add_argument("--sign-in", action="store_true", help=f"Turn sign-in on, open sign-up to anyone, and make {DEMO_ACCOUNT['email']} (password {DEMO_ACCOUNT['password']}) on an empty database.")
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
    if args.sign_in:
        sign_in_on(store)
    print(f"sopops ({args.models} models) on http://127.0.0.1:{args.port}, database {args.database_url}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
