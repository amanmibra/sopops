"""The whole walkthrough in one command: a fresh sopops, then every step in order, then the checklist.

    python examples/walkthrough/run_all.py                  # fake models; sopops stays up for the UI afterwards
    python examples/walkthrough/run_all.py --models real    # your models (see WALKTHROUGH.md for the variables)
    python examples/walkthrough/run_all.py --stop           # stop sopops when done (what the test does)
    WALKTHROUGH_ONBOARD=sopc python examples/walkthrough/run_all.py   # onboard from the existing sopc project instead

Starts `serve.py --fresh` on WALKTHROUGH_PORT (default 8585), then runs onboard.py, generate.py, backfill.py,
review.py, replay.py and verify.py. Exits with verify.py's status.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

import httpx

from common import HERE, PORT, URL

ONBOARD = os.environ.get("WALKTHROUGH_ONBOARD", "proposal")  # proposal (a coding agent's push) | sopc (an existing sopc project)
STEPS = [
    ("2. Onboard (prepared proposal) and publish release 1", ["onboard.py"]) if ONBOARD != "sopc" else
    ("2. Onboard (an existing sopc project) and publish release 1", ["onboard.py", "sopc"]),
    ("4. Generate the calls", ["generate.py"]),
    ("5. Backfill", ["backfill.py"]),
    ("6. Review patterns, verdicts and suggestions; publish release 2", ["review.py"]),
    ("7. Replay new calls through the SDK", ["replay.py"]),
    ("8. Verify", ["verify.py"]),
]


def up() -> bool:
    try:
        return httpx.get(f"{URL}/healthz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def run(script: list[str], extra: list[str]) -> int:
    return subprocess.call([sys.executable, str(HERE / script[0]), *script[1:], *extra], cwd=HERE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", choices=["fake", "real"], default=os.environ.get("WALKTHROUGH_MODELS", "fake"))
    parser.add_argument("--stop", action="store_true", help="Stop sopops when done.")
    parser.add_argument("--fast", action="store_true", help="Replay as fast as possible (for tests).")
    args = parser.parse_args()

    os.environ["WALKTHROUGH_MODELS"] = args.models
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            sys.exit(f"port {PORT} is taken: stop what's on it, or set WALKTHROUGH_PORT")
    server = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--fresh", "--models", args.models], cwd=HERE)
    status = 1
    try:
        while not up():
            if server.poll() is not None:
                sys.exit(f"sopops didn't start (is port {PORT} taken? set WALKTHROUGH_PORT)")
            time.sleep(0.3)
        for title, script in STEPS:
            print(f"\n== {title}", flush=True)
            extra = ["--rate", "50", "--speed", "2000"] if args.fast and script[0] == "replay.py" else []
            extra += ["--models", args.models] if script[0] == "verify.py" else []
            status = run(script, extra)
            if status and script[0] != "verify.py":
                sys.exit(f"{script[0]} failed ({status})")
    finally:
        if args.stop or status:
            server.terminate()
            server.wait(10)
        else:
            print(f"\nsopops is still running at {URL} (pid {server.pid}); open it to look around, Ctrl-C to stop.", flush=True)
            try:
                server.wait()
            except KeyboardInterrupt:
                server.terminate()
    sys.exit(status)


if __name__ == "__main__":
    main()
