"""What every walkthrough step shares: where sopops is, the workspace, and the files a step leaves for the next.

`WALKTHROUGH_PORT` (default 8585, on 127.0.0.1) and `WALKTHROUGH_WORKSPACE` (default `walkthrough`) say where;
`WALKTHROUGH_OUT` (default `examples/walkthrough/out`) holds the generated sessions, the ground truth and the tokens.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# The Python SDK from this clone, so the steps run without installing it (`pip install ./sdks/python` works too).
sys.path.insert(1, str(REPO / "sdks" / "python" / "src"))
PORT = int(os.environ.get("WALKTHROUGH_PORT", "8585"))
URL = f"http://127.0.0.1:{PORT}"
WS = os.environ.get("WALKTHROUGH_WORKSPACE", "walkthrough")
OUT = Path(os.environ.get("WALKTHROUGH_OUT") or HERE / "out")
API = f"{URL}/v1/workspaces/{WS}"
AGENTS = ["bayleaf-bistro", "juniper-grill", "copper-wok-eastside", "copper-wok-harbor"]


def client(timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(base_url=URL, timeout=timeout)


def call(http: httpx.Client, method: str, path: str, body: Any = None, **kw: Any) -> Any:
    """One request; a 4xx/5xx exits with sopops's message."""
    res = http.request(method, path.replace("{ws}", WS), json=body, **kw)
    if res.status_code >= 400:
        sys.exit(f"{method} {path}: {res.status_code} {res.text[:1000]}")
    return res.json() if res.content else None


def wait_until_up(seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if httpx.get(f"{URL}/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            sys.exit(f"sopops isn't answering at {URL}: start it with `just examples/walkthrough/serve`")
        time.sleep(0.3)


def save(name: str, data: Any) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def load(name: str) -> Any:
    path = OUT / name
    if not path.exists():
        sys.exit(f"{path} is missing: run the earlier steps first (just examples/walkthrough/all)")
    return json.loads(path.read_text())


def ingest_token(http: httpx.Client) -> str:
    """A workspace token with the ingest and runtime scopes, made once and kept in out/token.json."""
    path = OUT / "token.json"
    if path.exists():
        return json.loads(path.read_text())["token"]
    got = call(http, "POST", "/v1/workspaces/{ws}/tokens", {"name": "walkthrough agents", "scopes": ["ingest", "runtime"]})
    save("token.json", {"token": got["token"]})
    return got["token"]


def wait_for_pipeline(http: httpx.Client, expected: int, seconds: float = 600.0) -> dict[str, int]:
    """Wait until `expected` sessions exist and none is still being processed; returns the count per status."""
    deadline = time.monotonic() + seconds
    while True:
        counts: dict[str, int] = {}
        cursor = None
        while True:
            page = call(http, "GET", "/v1/workspaces/{ws}/sessions", params={"limit": 200, **({"cursor": cursor} if cursor else {})})
            for s in page["items"]:
                counts[s["status"]] = counts.get(s["status"], 0) + 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
        total = sum(counts.values())
        busy = sum(counts.get(s, 0) for s in ("received", "routed")) if total >= expected else 1
        if not busy:
            return counts
        if time.monotonic() > deadline:
            sys.exit(f"the pipeline didn't finish in {seconds:.0f}s: {counts}")
        time.sleep(1.0)


def all_sessions(http: httpx.Client, **params: Any) -> list[dict[str, Any]]:
    """Every session matching the list filters, following the cursor."""
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = call(http, "GET", "/v1/workspaces/{ws}/sessions", params={"limit": 200, **params, **({"cursor": cursor} if cursor else {})})
        out += page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            return out


def rule_key(rule: dict[str, Any] | None) -> str:
    return f"{rule['kind']}_{rule['index']}" if rule and rule.get("kind") else "?"


def match_modes(http: httpx.Client, truth: dict[str, Any], kind: str = "backfill") -> dict[str, dict[str, Any]]:
    """Each planted failure mode's pattern: of the patterns on its rule (the failure modes if the rule is split, else
    the rule pattern), the one whose sessions overlap the mode's the most (Jaccard at least 0.5). Names don't
    matter, so this works the same for fake and real models. {mode: {pattern, jaccard, sessions}}."""
    patterns = call(http, "GET", "/v1/workspaces/{ws}/patterns", params={"window": "365d"})["items"]
    ours = {sid for sid, s in truth["sessions"].items() if s["set"] == kind}
    found: dict[str, dict[str, Any]] = {}
    for p in patterns:
        planted = truth["expected"][kind].get(f"{p['procedure_id']}/{rule_key(p.get('rule'))}", {})
        if not planted:
            continue
        candidates = [s for s in p["sub_patterns"] if s["kind"] == "sub" and s["status"] == "active"] or [p]
        members = {c["pattern_id"]: {s["external_id"] for s in all_sessions(http, pattern=c["pattern_id"])} & ours for c in candidates}
        for mode, spec in planted.items():
            want = set(spec["sessions"])
            for c in candidates:
                got = members[c["pattern_id"]]
                j = len(want & got) / len(want | got) if want | got else 0.0
                if j >= 0.5 and j > found.get(mode, {}).get("jaccard", 0):
                    found[mode] = {"pattern": c, "rule_pattern": p, "jaccard": round(j, 2), "sessions": got}
    return found
