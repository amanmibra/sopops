"""sopops-mcp: an MCP server (stdio) for onboarding voice agents from a coding agent such as Claude Code or Codex.

The coding agent finds each agent's prompt wherever it lives (code, config, a database, a platform), turns the prompts
into a proposal (agent context, shared instructions, procedures, variables), checks fidelity, and pushes the proposal to
sopops for review. Optionally it writes the same proposal as a sopc project into the repo. sopops does the compiling and
checking over its HTTP API (`/v1/onboarding`); this server only reads platforms and writes files locally.

Install with the `mcp` extra: `uvx --from "sopops-client[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python" sopops-mcp`.

Environment (set by the MCP host, e.g. `claude mcp add sopops --env SOPOPS_URL=... --env SOPOPS_TOKEN=... -- ...`):

| Variable | Used for |
|---|---|
| `SOPOPS_URL`, `SOPOPS_TOKEN` | The sopops server and an onboarding token from its Import agents page. Without them, only the platform tools work. |
| `ELEVENLABS_API_KEY`, `VAPI_API_KEY` | Reading agents from those platforms (read-only). |
"""

from __future__ import annotations

import argparse
import functools
import os
import sys
from pathlib import Path
from typing import Any, Callable, TypeVar

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import platforms

INSTRUCTIONS = """Onboard voice agents into sopops. Start with get_kickoff and follow it. Only read from the user's systems and platforms;
never change, deploy or delete anything there. Report progress with report_progress as you go."""
NOT_CONNECTED = "Not connected to sopops: set SOPOPS_URL and SOPOPS_TOKEN (create a token on the Import agents page)."
SKILL_DIRS = {"claude": Path(".claude/skills"), "codex": Path(".agents/skills")}  # Codex (and OpenCode) read .agents/skills

USAGE = """usage: sopops-mcp                  the onboarding MCP server on stdio, as Claude Code or Codex runs it
       sopops-mcp install-skill    copy the sopops-onboard skill into this repo (--claude, --codex, --url URL)
"""

F = TypeVar("F", bound=Callable[..., Any])


class SopopsClient:
    """The sopops server, with the onboarding token."""

    def __init__(self, url: str | None, token: str | None, transport: httpx.BaseTransport | None = None):
        self.url = (url or "").rstrip("/") or None
        self.token = (token or "").strip() or None
        self.transport = transport

    @property
    def connected(self) -> bool:
        return bool(self.url and self.token)

    def call(self, method: str, path: str, body: Any = None, params: dict[str, str] | None = None) -> Any:
        if not self.connected:
            raise RuntimeError(NOT_CONNECTED)
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            with httpx.Client(base_url=self.url or "", timeout=120, transport=self.transport, headers=headers) as c:
                res = c.request(method, path, json=body, params=params)
        except httpx.HTTPError as e:
            raise RuntimeError(f"couldn't reach sopops at {self.url}: {type(e).__name__}") from None
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail", res.text)
            except ValueError:
                detail = res.text
            raise RuntimeError(f"sopops answered {res.status_code}: {detail}")
        return res.json()


def _key(platform: str, env: dict[str, str]) -> str:
    var = {"elevenlabs": "ELEVENLABS_API_KEY", "vapi": "VAPI_API_KEY"}.get(platform)
    if var is None:
        raise ValueError("LiveKit agents keep their prompt in code: read it from the repo (Agent(instructions=...) or the files it loads).")
    key = env.get(var, "").strip()
    if not key:
        raise ValueError(f"{var} isn't set in the MCP server's environment. Ask the user to add it (read-only is enough), or to paste the prompts.")
    return key


def _explained(fn: F) -> F:
    """Errors the host agent can act on reach it as tool errors with their message (others stay on the server)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, ValueError) as e:
            raise ToolError(str(e)) from e

    return wrapper  # type: ignore[return-value]


def make_server(env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None, platform_transport: httpx.BaseTransport | None = None) -> MCPServer:
    env = dict(os.environ) if env is None else env
    sopops = SopopsClient(env.get("SOPOPS_URL"), env.get("SOPOPS_TOKEN"), transport)
    server = MCPServer("sopops", instructions=INSTRUCTIONS)

    @server.tool()
    @_explained
    def get_kickoff() -> str:
        """Start here: how to onboard this project's agents into sopops (the sopops-onboard skill), and this session's
        workspace, server and mode."""
        got: dict[str, Any] = sopops.call("GET", "/v1/onboarding/kickoff", params={"server_url": sopops.url or ""})
        return str(got["instructions"])

    @server.tool()
    @_explained
    def get_extraction_guide() -> dict[str, Any]:
        """How to turn agent prompts into a sopops proposal (context, shared instructions, procedures, variables, drift),
        the proposal's JSON schema, and the onboarding session (workspace, hosted or repo mode)."""
        guide: dict[str, Any] = sopops.call("GET", "/v1/onboarding/guide")
        return {**guide, "session": sopops.call("GET", "/v1/onboarding/session"), "connected": True}

    @server.tool()
    @_explained
    def list_platform_agents(platform: str) -> list[dict[str, Any]]:
        """Agents on ElevenLabs or Vapi ([{id, name}]), read with the key in the MCP server's environment. Read-only."""
        return platforms.list_agents(platform, _key(platform, env), platform_transport)

    @server.tool()
    @_explained
    def get_agent_prompt(platform: str, id: str) -> dict[str, Any]:
        """One ElevenLabs or Vapi agent's prompt and tool names: {name, platform, platform_id, prompt, tools, notes}. Read-only."""
        return platforms.get_agent(platform, _key(platform, env), id, platform_transport)

    @server.tool()
    @_explained
    def propose_extraction(prompts: list[dict[str, Any]]) -> dict[str, Any]:
        """Start a proposal from agent prompts: [{name, platform, platform_id, prompt, tools}]. Returns `instructions`
        and the prompts split into numbered units for you to classify, and each agent's original prompt (`originals`)."""
        out: dict[str, Any] = sopops.call("POST", "/v1/onboarding/extraction", {"prompts": prompts})
        return out

    @server.tool()
    @_explained
    def check_fidelity(proposal: dict[str, Any], originals: dict[str, str]) -> dict[str, Any]:
        """Compile each proposed agent with sopc and compare it with its original prompt, sentence by sentence.
        Returns per agent: ok, score (share of sentences kept), missing and changed sentences, and sopc's issues."""
        out: dict[str, Any] = sopops.call("POST", "/v1/onboarding/fidelity", {"proposal": proposal, "originals": originals})
        return out

    @server.tool()
    @_explained
    def push_to_sopops(proposal: dict[str, Any], originals: dict[str, str], findings: list[dict[str, Any]] | None = None, notes: list[str] | None = None) -> dict[str, Any]:
        """Send the proposal to sopops for the user to review and apply as drafts. Pushing again replaces the last push.
        Nothing is published. Needs SOPOPS_URL and SOPOPS_TOKEN."""
        body = {"proposal": proposal, "originals": originals, "findings": findings or [], "notes": notes or []}
        out: dict[str, Any] = sopops.call("PUT", "/v1/onboarding/proposal", body)
        session = sopops.call("GET", "/v1/onboarding/session")
        out["review"] = f"{sopops.url}/w/{session['workspace']}/import/{session['import_id']}"
        return out

    @server.tool()
    @_explained
    def report_progress(step: str, message: str) -> dict[str, Any]:
        """Tell the person watching the sopops Import agents page what you're doing (one short sentence)."""
        if not sopops.connected:
            return {"ok": False, "reason": "not connected to sopops (no SOPOPS_URL/SOPOPS_TOKEN); progress is only shown in sopops"}
        sopops.call("POST", "/v1/onboarding/progress", {"step": step[:64], "message": message[:4000]})
        return {"ok": True}

    @server.tool()
    @_explained
    def report_pull_request(url: str) -> dict[str, Any]:
        """Repo mode: tell sopops the URL of the onboarding pull request you opened (adding the sopc project). sopops
        follows it, and pulls the folder into drafts when the user merges it."""
        got = sopops.call("POST", "/v1/onboarding/pull-request", {"url": url})
        return {"pr_url": got["pr_url"], "pr_state": got["pr_state"], "next": "Tell the user the pull request is open for their review; don't merge it."}

    @server.tool()
    @_explained
    def open_pull_request(description: str = "") -> dict[str, Any]:
        """Repo mode, when the kickoff says sopops opens the pull request (a GitHub App connection): sopops writes your
        pushed proposal as a sopc project at the connected folder, opens the onboarding pull request, and follows it.
        Push the proposal first. Don't run git or gh for it."""
        got = sopops.call("POST", "/v1/onboarding/pull-request/open", {"description": description or None})
        return {"pr_url": got["pr_url"], "pr_state": got["pr_state"], "next": "Tell the user the pull request is open for their review; don't merge it."}

    @server.tool()
    @_explained
    def write_sopc_project(proposal: dict[str, Any], path: str = "sops") -> dict[str, Any]:
        """Write the proposal as a sopc project (sopc.yaml, instructions/, procedures/, agents/) at `path`, relative to the
        current directory. sopops writes the files with sopc and checks them. Existing files with the same names are
        overwritten; other files are left alone."""
        got = sopops.call("POST", "/v1/onboarding/project", {"proposal": proposal})
        root = Path(path).expanduser()
        if not root.is_absolute():
            root = Path(os.getcwd()) / root
        written = []
        for rel, text in sorted(got["files"].items()):
            dest = (root / rel).resolve()
            if not dest.is_relative_to(root.resolve()):
                raise ValueError(f"sopops sent a file outside {root}: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            written.append(str(dest))
        return {"written": written, "issues": got["issues"], "sopc_validate": got["validation"]}

    return server


def install_skill(root: Path, agents: list[str], url: str, transport: httpx.BaseTransport | None = None) -> list[Path]:
    """Copy the sopops-onboard skill (SKILL.md, from the sopops server at `url`, so it matches that server) into the repo
    at `root` for each agent (`claude`, `codex`). Returns the files written."""
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=30, transport=transport) as c:
            res = c.get("/v1/onboarding/skill")
    except httpx.HTTPError as e:
        raise RuntimeError(f"couldn't reach sopops at {url}: {type(e).__name__}") from None
    if res.status_code >= 400:
        raise RuntimeError(f"sopops answered {res.status_code} for the skill")
    skill = res.json()
    out = []
    for agent in agents:
        target = root / SKILL_DIRS[agent] / skill["name"] / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill["text"])
        out.append(target)
    return out


def main(argv: list[str] | None = None) -> None:
    """`sopops-mcp`: the MCP server on stdio. `sopops-mcp install-skill [--claude] [--codex] [--url URL]`: copy the
    onboarding skill into this repo (.claude/skills, .agents/skills; both by default)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install-skill"]:
        parser = argparse.ArgumentParser(prog="sopops-mcp install-skill", description="Copy the sopops-onboard skill into this repo.")
        parser.add_argument("--claude", action="store_true", help="Claude Code: .claude/skills/sopops-onboard/SKILL.md")
        parser.add_argument("--codex", action="store_true", help="Codex (and OpenCode): .agents/skills/sopops-onboard/SKILL.md")
        parser.add_argument("--url", default=os.environ.get("SOPOPS_URL"), help="The sopops server to copy it from (default: SOPOPS_URL).")
        args = parser.parse_args(argv[1:])
        if not args.url:
            parser.error("pass --url or set SOPOPS_URL to your sopops server")
        agents = [a for a in ("claude", "codex") if getattr(args, a)] or ["claude", "codex"]
        try:
            paths = install_skill(Path.cwd(), agents, args.url)
        except RuntimeError as e:
            parser.exit(1, f"sopops-mcp: {e}\n")
        for path in paths:
            print(f"wrote {path}")
        return
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    make_server().run("stdio")


if __name__ == "__main__":
    main()
