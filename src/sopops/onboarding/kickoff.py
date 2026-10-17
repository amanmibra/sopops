"""What the user pastes to start a coding agent (Claude Code or Codex) on onboarding: the MCP setup command with the
server URL and onboarding token filled in, the prompt (`kickoff.md`), and recommended settings (editable text)."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from string import Template

TEMPLATE = Path(__file__).parent / "kickoff.md"
# Until sopops is on PyPI, uvx installs the MCP server from the repo. SOPOPS_MCP_SOURCE overrides it.
DEFAULT_SOURCE = "git+https://github.com/amanmibra/sopops@onboarding"

REPO_STEP = """6. Repo setup (the user opted in; target: $repo): write the same proposal as a sopc project at `sops/` with
   `write_sopc_project`, run `sopc validate` there if the sopc CLI is installed, and open a pull request (with `gh pr create`
   if it's available, otherwise say how) titled "Add sopc project for sopops". In its description: the agents and where
   each prompt was found, the blocks extracted, drift found, and each agent's fidelity score. Don't merge it.
"""

SETTINGS = (
    "Claude Code: use the strongest model you have (for example /model opus) and accept edits automatically (Shift+Tab).\n"
    "Codex: high reasoning effort and auto mode (codex -c model_reasoning_effort=high, then /approvals: Auto)."
)


def mcp_source() -> str:
    return os.environ.get("SOPOPS_MCP_SOURCE") or DEFAULT_SOURCE


def prompt(workspace: str, server_url: str, mode: str = "hosted", repo: str | None = None) -> str:
    repo_step = Template(REPO_STEP).safe_substitute(repo=repo or "this repository") if mode == "repo" else ""
    return Template(TEMPLATE.read_text()).safe_substitute(workspace=workspace, server_url=server_url, repo_step=repo_step).replace("\n\n\n", "\n\n").strip() + "\n"


def kickoff(workspace: str, server_url: str, token: str, mode: str = "hosted", repo: str | None = None) -> dict:
    url = server_url.rstrip("/")
    source = mcp_source()
    env = ["--env", f"SOPOPS_URL={url}", "--env", f"SOPOPS_TOKEN={token}"]
    run = ["--", "uvx", "--from", source, "sopops-mcp"]
    toml = (
        "[mcp_servers.sopops]\n"
        'command = "uvx"\n'
        f'args = ["--from", "{source}", "sopops-mcp"]\n'
        f'env = {{ SOPOPS_URL = "{url}", SOPOPS_TOKEN = "{token}" }}\n'
    )
    return {
        "claude_code": shlex.join(["claude", "mcp", "add", "sopops", *env, *run]),
        "codex": shlex.join(["codex", "mcp", "add", "sopops", *env, *run]),
        "codex_toml": toml,
        "prompt": prompt(workspace, url, mode, repo),
        "settings": SETTINGS,
        "mcp_source": source,
    }
