"""sopops-mcp: an MCP server (stdio) for onboarding voice agents from a coding agent such as Claude Code or Codex.

The coding agent finds each agent's prompt wherever it lives (code, config, a database, a platform), turns the prompts
into a proposal (agent context, shared instructions, procedures, variables), checks fidelity, and pushes the proposal to
sopops for review. Optionally it writes the same proposal as a sopc project into the repo.

Environment (set by the MCP host, e.g. `claude mcp add sopops --env SOPOPS_URL=... --env SOPOPS_TOKEN=... -- ...`):

| Variable | Used for |
|---|---|
| `SOPOPS_URL`, `SOPOPS_TOKEN` | The sopops server and an onboarding token from its Import agents page. Without them, only the local tools work. |
| `ELEVENLABS_API_KEY`, `VAPI_API_KEY` | Reading agents from those platforms (read-only). |
| `SOPC_BIN` | A local sopc binary, for `check_fidelity` and `write_sopc_project` without a server. |
| `SOPOPS_MODEL_REASONING`, `SOPOPS_MODEL_FAST` | Optional: let `propose_extraction` run sopops's own extraction locally. Without them, it returns the instructions and the host agent does the reasoning. |
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from .ai.lm import AI, RULES, Models, model_for
from .ai.signatures import SegmentPrompt, StructureProcedure
from .onboarding import fidelity, kickoff, platforms
from .onboarding import text as tx
from .onboarding.fidelity import files_of
from .onboarding.pipeline import extract, guide_text
from .onboarding.rules import RulesModels
from .onboarding.types import Finding, Proposal, ProposalContent, SourcePrompt
from .sopc import Invalid, Sopc, SopcUnavailable

INSTRUCTIONS = """Onboard voice agents into sopops. Start with get_kickoff and follow it. Only read from the user's systems and platforms;
never change, deploy or delete anything there. Report progress with report_progress as you go."""


class SopopsClient:
    """The sopops server, with the onboarding token."""

    def __init__(self, url: str | None, token: str | None, transport: httpx.BaseTransport | None = None):
        self.url = (url or "").rstrip("/") or None
        self.token = (token or "").strip() or None
        self.transport = transport

    @property
    def connected(self) -> bool:
        return bool(self.url and self.token)

    def call(self, method: str, path: str, body: Any = None) -> Any:
        if not self.connected:
            raise RuntimeError("Not connected to sopops: set SOPOPS_URL and SOPOPS_TOKEN (create a token on the Import agents page).")
        with httpx.Client(base_url=self.url or "", timeout=120, transport=self.transport, headers={"Authorization": f"Bearer {self.token}"}) as c:
            res = c.request(method, path, json=body)
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


def _sopc(env: dict[str, str]) -> Sopc | None:
    binary = env.get("SOPC_BIN") or shutil.which("sopc")
    return Sopc(binary) if binary and Path(binary).exists() else None


def _content(proposal: dict) -> ProposalContent:
    return ProposalContent.model_validate(proposal)


def _explained(fn: Callable) -> Callable:
    """Errors the host agent can act on reach it as tool errors with their message (others stay on the server)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (RuntimeError, ValueError, platforms.PlatformError, Invalid, SopcUnavailable, ValidationError) as e:
            raise ToolError(str(e)) from e

    return wrapper


def make_server(env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None, platform_transport: httpx.BaseTransport | None = None) -> MCPServer:
    env = dict(os.environ) if env is None else env
    sopops = SopopsClient(env.get("SOPOPS_URL"), env.get("SOPOPS_TOKEN"), transport)
    server = MCPServer("sopops", instructions=INSTRUCTIONS)

    @server.tool()
    @_explained
    def get_kickoff() -> str:
        """Start here: how to onboard this project's agents into sopops (the sopops-onboard skill), and this session's
        workspace, server and mode."""
        session = None
        if sopops.connected:
            session = {**sopops.call("GET", "/v1/onboarding/session"), "server_url": sopops.url}
        return kickoff.instructions(session)

    @server.tool()
    @_explained
    def get_extraction_guide() -> dict:
        """How to turn agent prompts into a sopops proposal (context, shared instructions, procedures, variables, drift),
        the proposal's JSON schema, and the onboarding session (workspace, hosted or repo mode) if connected."""
        session = None
        if sopops.connected:
            try:
                session = sopops.call("GET", "/v1/onboarding/session")
            except RuntimeError as e:
                session = {"error": str(e)}
        return {
            "guide": guide_text(),
            "proposal_schema": ProposalContent.model_json_schema(),
            "finding_schema": Finding.model_json_schema(),
            "session": session,
            "connected": sopops.connected,
        }

    @server.tool()
    @_explained
    def list_platform_agents(platform: str) -> list[dict]:
        """Agents on ElevenLabs or Vapi ([{id, name}]), read with the key in the MCP server's environment. Read-only."""
        return platforms.list_agents(platform, _key(platform, env), platform_transport)

    @server.tool()
    @_explained
    def get_agent_prompt(platform: str, id: str) -> dict:
        """One ElevenLabs or Vapi agent's prompt and tool names: {name, platform, platform_id, prompt, tools, notes}. Read-only."""
        return platforms.get_agent(platform, _key(platform, env), id, platform_transport)

    @server.tool()
    @_explained
    def propose_extraction(prompts: list[dict]) -> dict:
        """Start a proposal from agent prompts: [{name, platform, platform_id, prompt, tools}].
        With a model configured for sopops in this environment, sopops extracts it and returns `proposal`.
        Otherwise it returns `instructions` and the prompts split into numbered units for you to classify yourself."""
        sources = [SourcePrompt.model_validate(p) for p in prompts]
        ai = _ai_for(env)
        if ai is not None:
            proposal = extract(sources, ai, _sopc(env))
            return {"mode": "extracted", "proposal": proposal.model_dump(mode="json")}
        ids = [tx.unique(tx.slug(s.name, "agent"), set()) for s in sources]
        return {
            "mode": "host",
            "instructions": guide_text() + "\n\n## How to classify\n\n" + SegmentPrompt.instructions + "\n\n## Procedures\n\n" + StructureProcedure.instructions,
            "inputs": [
                {"id": i, "name": s.name, "platform": s.platform, "platform_id": s.platform_id or i, "tools": s.tools,
                 "units": [{"n": n + 1, "text": u} for n, u in enumerate(tx.split_units(s.prompt))]}
                for i, s in zip(ids, sources)
            ],
            "originals": {i: s.prompt for i, s in zip(ids, sources)},
            "proposal_schema": ProposalContent.model_json_schema(),
            "next": "Build the proposal from these units, then call check_fidelity(proposal, originals).",
        }

    @server.tool()
    @_explained
    def check_fidelity(proposal: dict, originals: dict[str, str]) -> dict:
        """Compile each proposed agent with sopc and compare it with its original prompt, sentence by sentence.
        Returns per agent: ok, score (share of sentences kept), missing and changed sentences, and sopc's issues."""
        content = _content(proposal)
        if sopops.connected:
            return sopops.call("POST", "/v1/onboarding/fidelity", {"proposal": content.model_dump(mode="json"), "originals": originals})
        sopc = _sopc(env)
        if sopc is None:
            raise RuntimeError("No sopops connection and no sopc binary: set SOPOPS_URL and SOPOPS_TOKEN, or install sopc (SOPC_BIN).")
        checked = fidelity.check(Proposal(**content.model_dump(), originals=originals), sopc)
        return {"fidelity": [f.model_dump(exclude={"compiled"}) for f in checked.fidelity], "issues": [i.model_dump() for i in checked.issues]}

    @server.tool()
    @_explained
    def push_to_sopops(proposal: dict, originals: dict[str, str], findings: list[dict] | None = None, notes: list[str] | None = None) -> dict:
        """Send the proposal to sopops for the user to review and apply as drafts. Pushing again replaces the last push.
        Nothing is published. Needs SOPOPS_URL and SOPOPS_TOKEN."""
        body = {
            "proposal": _content(proposal).model_dump(mode="json"), "originals": originals,
            "findings": [Finding.model_validate(f).model_dump(mode="json") for f in findings or []], "notes": notes or [],
        }
        out = sopops.call("PUT", "/v1/onboarding/proposal", body)
        session = sopops.call("GET", "/v1/onboarding/session")
        out["review"] = f"{sopops.url}/w/{session['workspace']}/import/{session['import_id']}"
        return out

    @server.tool()
    @_explained
    def report_progress(step: str, message: str) -> dict:
        """Tell the person watching the sopops Import agents page what you're doing (one short sentence)."""
        if not sopops.connected:
            return {"ok": False, "reason": "not connected to sopops (no SOPOPS_URL/SOPOPS_TOKEN); progress is only shown in sopops"}
        sopops.call("POST", "/v1/onboarding/progress", {"step": step[:64], "message": message[:4000]})
        return {"ok": True}

    @server.tool()
    @_explained
    def write_sopc_project(proposal: dict, path: str = "sops") -> dict:
        """Write the proposal as a sopc project (sopc.yaml, instructions/, procedures/, agents/) at `path`, relative to the
        current directory. Existing files with the same names are overwritten; other files are left alone."""
        content = _content(proposal)
        sopc = _sopc(env)
        files, issues = files_of(content, sopc.export if sopc else None)
        root = Path(path).expanduser()
        if not root.is_absolute():
            root = Path(os.getcwd()) / root
        written = []
        for rel, text in sorted(files.items()):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            written.append(str(dest))
        validation = None
        if sopc is not None:
            validation = [i.to_dict() for i in sopc.validate(files)]
        return {"written": written, "issues": [i.to_dict() for i in issues], "sopc_validate": validation}

    return server


def _ai_for(env: dict[str, str]) -> AI | None:
    model = model_for("reasoning", env)
    if model == RULES:
        return RulesModels()
    return Models({"reasoning": model, "fast": model_for("fast", env) or model}) if model else None


def main(argv: list[str] | None = None) -> None:
    """`sopops-mcp`: the MCP server on stdio. `sopops-mcp install-skill [--claude] [--codex]`: copy the onboarding skill
    into this repo (.claude/skills, .agents/skills; both by default)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install-skill"]:
        parser = argparse.ArgumentParser(prog="sopops-mcp install-skill", description="Copy the sopops-onboard skill into this repo.")
        parser.add_argument("--claude", action="store_true", help="Claude Code: .claude/skills/sopops-onboard/SKILL.md")
        parser.add_argument("--codex", action="store_true", help="Codex (and OpenCode): .agents/skills/sopops-onboard/SKILL.md")
        args = parser.parse_args(argv[1:])
        agents = [a for a in ("claude", "codex") if getattr(args, a)] or ["claude", "codex"]
        for path in kickoff.install_skill(Path.cwd(), agents):
            print(f"wrote {path}")
        return
    make_server().run("stdio")


if __name__ == "__main__":
    main()
