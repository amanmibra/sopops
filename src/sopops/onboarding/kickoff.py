"""Starting a coding agent (Claude Code or Codex) on onboarding.

The UI gives one command per agent that adds the sopops MCP server (server URL and onboarding token filled in) and
starts the agent with a short prompt: call `get_kickoff` and follow it. `get_kickoff` returns the onboarding skill
(`skills/sopops-onboard/SKILL.md`, the single source of the instructions, bundled with the package so it matches the
installed version) plus this session's details: workspace, server, mode, and in repo mode the connected repository and
what to do there: open the onboarding pull request adding the sopc project when the folder has none sopc can build (with
a GitHub App connection, sopops opens it from the pushed proposal; with a token, the agent opens it with `gh`).
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

# Until sopops is on PyPI, uvx installs the MCP server from the repo. SOPOPS_MCP_SOURCE overrides it.
DEFAULT_SOURCE = "git+https://github.com/amanmibra/sopops@main"
SKILL_NAME = "sopops-onboard"
PROMPT = "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it."
# Installed as a skill (`sopops-mcp install-skill`), the prompt can be the skill itself.
SKILL_PROMPT = {"claude": f"/{SKILL_NAME}", "codex": f"${SKILL_NAME}"}
SKILL_DIRS = {"claude": Path(".claude/skills"), "codex": Path(".agents/skills")}  # Codex (and OpenCode) read .agents/skills

REPO_STEP = """Repo mode (the user opted in). sopops is connected to GitHub: `{repo}`, branch `{branch}`, folder `{folder}`, which {finding}.
After pushing, write the same proposal as a sopc project at `{folder}` with `write_sopc_project`, run `sopc validate` there if the
sopc CLI is installed, and open a pull request into `{branch}` from a new branch (with `gh pr create` if it's available,
otherwise say how) titled "Add sopc project for sopops". In its description: the agents and where each prompt was found,
the blocks extracted, drift found, and each agent's fidelity score. Don't merge it. Then call `report_pull_request` with its
URL: sopops follows it, and when the user merges it, pulls `{folder}` into drafts."""

REPO_STEP_APP = """Repo mode (the user opted in). sopops is connected to GitHub through its GitHub App: `{repo}`, branch `{branch}`,
folder `{folder}`, which {finding}. sopops opens the onboarding pull request itself, so don't run git or `gh` for it. After pushing,
call `open_pull_request` with a description: the agents and where each prompt was found, the blocks extracted, and drift found
(sopops adds each agent's fidelity score). It opens a pull request into `{branch}` writing your proposal at `{folder}`; tell the user
it's open for their review, and don't merge it. When the user merges it, sopops pulls `{folder}` into drafts."""

REPO_FINDING = {
    "missing": "doesn't hold a sopc project yet",
    "invalid": "holds a sopc project sopc can't build; replace its files with yours",
}
REPO_VALID = """Repo mode: `{repo}` already holds a sopc project at `{folder}` on `{branch}`, and sopops reads it from there. Push
your proposal for review as usual, but don't open a pull request."""

SETTINGS = (
    "Claude Code: use the strongest model you have (for example /model opus) and accept edits automatically (Shift+Tab).\n"
    "Codex: high reasoning effort and auto mode (codex -c model_reasoning_effort=high, then /approvals: Auto)."
)


def mcp_source() -> str:
    return os.environ.get("SOPOPS_MCP_SOURCE") or DEFAULT_SOURCE


def skill_path() -> Path:
    """SKILL.md: bundled in the package (wheels, uvx), else the repo's `skills/` (a clone or an editable install)."""
    bundled = Path(__file__).parent / "SKILL.md"
    return bundled if bundled.is_file() else Path(__file__).resolve().parents[3] / "skills" / SKILL_NAME / "SKILL.md"


def skill_text() -> str:
    return skill_path().read_text()


def skill_body() -> str:
    """SKILL.md without its frontmatter."""
    return re.sub(r"\A---\n.*?\n---\n", "", skill_text(), count=1, flags=re.S).strip() + "\n"


def instructions(session: dict | None) -> str:
    """What `get_kickoff` returns: the skill, then this session's details (when the MCP server is connected)."""
    if not session or "workspace" not in session:
        return skill_body() + "\n## This session\n\nNot connected to sopops (no SOPOPS_URL / SOPOPS_TOKEN): you can extract and check locally, but not push.\n"
    lines = ["", "## This session", "", f"- Workspace: `{session['workspace']}`.", f"- Mode: {session.get('mode', 'hosted')}."]
    if session.get("server_url"):
        lines.append(f"- sopops: {session['server_url']}")
    if session.get("mode") == "repo":
        lines += ["", repo_step(session)]
    return skill_body() + "\n".join(lines) + "\n"


def repo_step(session: dict) -> str:
    """What to do in the repository: the onboarding pull request, unless the folder already holds a project sopc builds."""
    path = session.get("repo_path")
    where = {"repo": session.get("repo") or "this repository", "branch": session.get("repo_branch") or "main",
             "folder": f"{path}/" if path else ("the repository root" if path == "" else "sops/")}
    if session.get("repo_project") == "valid":
        return REPO_VALID.format(**where)
    step = REPO_STEP_APP if session.get("repo_auth") == "app" else REPO_STEP
    return step.format(**where, finding=REPO_FINDING.get(session.get("repo_project") or "missing", REPO_FINDING["missing"]))


def install_skill(root: Path, agents: list[str]) -> list[Path]:
    """Copy SKILL.md into the repo for each agent (`claude`, `codex`). Returns the files written."""
    out = []
    for agent in agents:
        target = root / SKILL_DIRS[agent] / SKILL_NAME / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_text())
        out.append(target)
    return out


def kickoff(workspace: str, server_url: str, token: str) -> dict:
    """The one-line commands (add the MCP server, then start the agent on the prompt), the Codex config as TOML, and
    recommended settings. The workspace, mode and repository reach the agent through `get_kickoff`."""
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
        "claude_code": shlex.join(["claude", "mcp", "add", "sopops", *env, *run]) + " && " + shlex.join(["claude", PROMPT]),
        "codex": shlex.join(["codex", "mcp", "add", "sopops", *env, *run]) + " && " + shlex.join(["codex", PROMPT]),
        "codex_toml": toml,
        "prompt": PROMPT,
        "settings": SETTINGS,
        "mcp_source": source,
    }
