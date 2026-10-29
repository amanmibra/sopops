"""sopops-mcp: an MCP server for onboarding voice agents from a coding agent such as Claude Code or Codex.

The coding agent finds each agent's instructions wherever they live (code, config, a database, a platform), turns them
into a proposal (agent context, shared instructions, procedures, variables), checks fidelity, and pushes the proposal to
sopops for review. Optionally it writes the same proposal as a sop project into the repo. sopops does the compiling and
checking over its HTTP API (`/v1/onboarding`); this server only reads platforms and writes files locally.

Install with the `mcp` extra: `uvx --from "sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python" sopops-mcp`.

It runs two ways:

- **stdio** (`sopops-mcp`): the coding agent starts it locally. Environment, set by the MCP host (for example
  `claude mcp add sopops --env SOPOPS_URL=... --env SOPOPS_TOKEN=... -- ...`):

  | Variable | Used for |
  |---|---|
  | `SOPOPS_URL`, `SOPOPS_TOKEN` | The sopops server and an onboarding token from its Import agents page, or a workspace token with the `config` scope. Without them, only the platform tools work. |
  | `SOPOPS_WORKSPACE` | The workspace the SOP tools edit, with a `config` token (an onboarding token knows its own). |
  | `ELEVENLABS_API_KEY`, `VAPI_API_KEY` | Reading agents from those platforms (read-only). |

- **Streamable HTTP** (`sopops-mcp serve`): one shared server, for example https://sopops.dev/mcp. It holds no
  credential. Each request brings its caller's own: `Authorization: Bearer <sopops token>` (required, forwarded to the
  sopops API as is), and optionally `X-SopOps-Workspace`, `X-ElevenLabs-Api-Key` and `X-Vapi-Api-Key`, which stand in
  for the variables above and are used for that request only. The server's environment only says where sopops is:
  `SOPOPS_URL` (the API it calls) and `SOPOPS_APP_URL` (where people open sopops, for links; default `SOPOPS_URL`).
  `write_sop_project` can't reach the caller's repo from there, so it returns the files for the agent to write.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import quote

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult

from . import platforms

INSTRUCTIONS = """Onboard voice agents into sopops, and keep their SOPs up to date afterwards.

Onboarding: start with get_kickoff and follow it. Only read from the user's systems and platforms; never change,
deploy or delete anything there. Report progress with report_progress as you go.

Later, to change what agents are told: list_sops, get_sop, get_sop_settings, save_sop, save_sop_settings and
preview_agent (a `config` token and SOPOPS_WORKSPACE, or an onboarding token). Every save or deletion is a draft.
Nothing reaches a call until someone publishes a release in sopops, so say that when you report back."""
NOT_CONNECTED = "Not connected to sopops: set SOPOPS_URL and SOPOPS_TOKEN (create a token on the Import agents page)."
NOT_CONNECTED_REMOTE = "Not connected to sopops: send your sopops token as the header `Authorization: Bearer <token>`."
ONBOARD_SKILL, AB_SKILL = "sopops-onboard", "sopops-ab-tests"
SKILLS = (ONBOARD_SKILL, AB_SKILL)
SKILL_DIRS = {"claude": Path(".claude/skills"), "codex": Path(".agents/skills")}  # Codex (and OpenCode) read .agents/skills

USAGE = """usage: sopops-mcp                  the onboarding MCP server on stdio, as Claude Code or Codex runs it
       sopops-mcp serve            the same tools over Streamable HTTP at /mcp for many users, each request with its
                                   own Authorization: Bearer token (--host, --port, --path; or HOST, PORT)
       sopops-mcp install-skill    copy sopops's skills (onboarding, A/B tests) into this repo (--claude, --codex, --url URL)
"""
PLATFORM_KEYS = {"elevenlabs": "ELEVENLABS_API_KEY", "vapi": "VAPI_API_KEY"}
PLATFORM_HEADERS = {"ELEVENLABS_API_KEY": "x-elevenlabs-api-key", "VAPI_API_KEY": "x-vapi-api-key"}
WORKSPACE_HEADER = "x-sopops-workspace"

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Caller:
    """Who one tool call is for: their sopops token and workspace, platform keys, and coding agent. On stdio it comes
    from the environment; over HTTP from that request's headers, so concurrent callers never see each other's."""

    token: str | None = None
    workspace: str | None = None
    keys: Mapping[str, str] = field(default_factory=dict)
    """ELEVENLABS_API_KEY, VAPI_API_KEY."""
    client: tuple[str, str] | None = None
    """The coding agent's MCP clientInfo (name, version), sent along so sopops can say which agent connected."""


_CALLER: ContextVar[Caller] = ContextVar("sopops_mcp_caller", default=Caller())


class SopOpsClient:
    """The sopops server, called with the current caller's token."""

    def __init__(self, url: str | None, transport: httpx.BaseTransport | None = None, remote: bool = False, app_url: str | None = None):
        self.url = (url or "").rstrip("/") or None
        self.app_url = (app_url or "").rstrip("/") or self.url
        """Where people open sopops, for the links tool results give (the review page, the kickoff's server)."""
        self.transport = transport
        self.remote = remote

    @property
    def token(self) -> str | None:
        return _CALLER.get().token

    @property
    def connected(self) -> bool:
        return bool(self.url and self.token)

    def call(self, method: str, path: str, body: Any = None, params: dict[str, str] | None = None) -> Any:
        if not self.connected:
            raise RuntimeError(NOT_CONNECTED_REMOTE if self.remote else NOT_CONNECTED)
        headers = {"Authorization": f"Bearer {self.token}"}
        client = _CALLER.get().client
        if client:
            headers.update({"X-SopOps-Client": client[0], "X-SopOps-Client-Version": client[1]})
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


class _Server(MCPServer):
    """Sets the caller for each tool call (token, workspace, platform keys, and the MCP client's clientInfo: Claude
    Code, Codex, ...), for the requests to sopops and the platforms."""

    def __init__(self, env: Mapping[str, str], remote: bool):
        super().__init__("sopops", instructions=INSTRUCTIONS)
        self.env = env
        self.remote = remote

    def caller(self, context: Context[Any, Any] | None) -> Caller:
        info = _client_info(context)
        if not self.remote:
            keys = {k: v for k in PLATFORM_HEADERS if (v := self.env.get(k, "").strip())}
            return Caller(_clean(self.env.get("SOPOPS_TOKEN")), _clean(self.env.get("SOPOPS_WORKSPACE")), keys, info)
        headers = _headers(context)
        keys = {k: v for k, h in PLATFORM_HEADERS.items() if (v := (headers.get(h) or "").strip())}
        return Caller(bearer(headers.get("authorization")), _clean(headers.get(WORKSPACE_HEADER)), keys, info)

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None) -> CallToolResult | InputRequiredResult:
        reset = _CALLER.set(self.caller(context))
        try:
            return await super().call_tool(name, arguments, context)
        finally:
            _CALLER.reset(reset)


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def bearer(authorization: str | None) -> str | None:
    """The token in an `Authorization: Bearer <token>` header, or None."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    return _clean(token) if scheme.lower() == "bearer" else None


def _headers(context: Context[Any, Any] | None) -> Mapping[str, str]:
    """This request's HTTP headers by lower-cased name (none on stdio or a direct call)."""
    try:
        headers = context.headers if context is not None else None
    except ValueError:  # no request (a direct call, as in tests)
        return {}
    return {k.lower(): v for k, v in headers.items()} if headers else {}


def _client_info(context: Context[Any, Any] | None) -> tuple[str, str] | None:
    try:
        params = context.session.client_params if context is not None else None
    except ValueError:  # no request (a direct call, as in tests)
        return None
    info = params.client_info if params is not None else None
    return (info.name, info.version) if info is not None and info.name else None


def _key(platform: str, remote: bool) -> str:
    var = PLATFORM_KEYS.get(platform)
    if var is None:
        raise ValueError("LiveKit agents keep their instructions in code: read them from the repo (Agent(instructions=...) or the files it loads).")
    key = _CALLER.get().keys.get(var, "")
    if not key:
        where = f"sent as the {PLATFORM_HEADERS[var]} header" if remote else "set in the MCP server's environment"
        raise ValueError(f"{var} isn't {where}. Ask the user to add it (read-only is enough), or to paste the agents' instructions.")
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


def make_server(env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None, platform_transport: httpx.BaseTransport | None = None, remote: bool = False) -> MCPServer:
    """The MCP server. `remote`: served over HTTP to many callers, each sending their own credentials as headers (see
    the module docstring); the environment then only says where sopops is, and the server holds no token."""
    env = dict(os.environ) if env is None else env
    sopops = SopOpsClient(env.get("SOPOPS_URL"), transport, remote=remote, app_url=env.get("SOPOPS_APP_URL"))
    server = _Server(env, remote)

    @server.tool()
    @_explained
    def get_kickoff() -> str:
        """Start here: how to onboard this project's agents into sopops (the sopops-onboard skill), and this session's
        workspace, server and mode."""
        got: dict[str, Any] = sopops.call("GET", "/v1/onboarding/kickoff", params={"server_url": sopops.app_url or ""})
        return str(got["instructions"])

    @server.tool()
    @_explained
    def get_extraction_guide() -> dict[str, Any]:
        """How to turn agents' instructions into a sopops proposal (context, shared instructions, procedures, variables, drift),
        the proposal's JSON schema, and the onboarding session (workspace, hosted or repo mode)."""
        guide: dict[str, Any] = sopops.call("GET", "/v1/onboarding/guide")
        return {**guide, "session": sopops.call("GET", "/v1/onboarding/session"), "connected": True}

    @server.tool()
    @_explained
    def get_ab_test_guide() -> str:
        """How to set up an A/B test of this project's voice agent (the sopops-ab-tests skill): find where its LLM, voice
        and speech models are set, add a stable traffic split, and make each call report its version to sopops."""
        got: dict[str, Any] = sopops.call("GET", "/v1/onboarding/skill", params={"name": AB_SKILL})
        return re.sub(r"\A---\n.*?\n---\n", "", str(got["text"]), count=1, flags=re.S).strip() + "\n"

    @server.tool()
    @_explained
    def list_platform_agents(platform: str) -> list[dict[str, Any]]:
        """Agents on ElevenLabs or Vapi ([{id, name}]), read with the user's key for that platform. Read-only."""
        return platforms.list_agents(platform, _key(platform, remote), platform_transport)

    @server.tool()
    @_explained
    def get_agent_prompt(platform: str, id: str) -> dict[str, Any]:
        """One ElevenLabs or Vapi agent's instructions (its system prompt) and tool names: {name, platform, platform_id, prompt, tools, notes}. Read-only."""
        return platforms.get_agent(platform, _key(platform, remote), id, platform_transport)

    @server.tool()
    @_explained
    def propose_extraction(prompts: list[dict[str, Any]]) -> dict[str, Any]:
        """Start a proposal from agents' instructions: [{name, platform, platform_id, prompt, tools}]. Returns `instructions`
        (the extraction guide) and each agent's instructions split into numbered units for you to classify, and each agent's
        original instructions (`originals`)."""
        out: dict[str, Any] = sopops.call("POST", "/v1/onboarding/extraction", {"prompts": prompts})
        return out

    @server.tool()
    @_explained
    def check_fidelity(proposal: dict[str, Any], originals: dict[str, str]) -> dict[str, Any]:
        """Compile each proposed agent with sop and compare it with its original instructions, sentence by sentence.
        Returns per agent: ok, score (share of sentences kept), missing and changed sentences, and sop's issues."""
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
        out["review"] = f"{sopops.app_url}/w/{session['workspace']}/import/{session['import_id']}"
        return out

    # --- keeping SOPs up to date, any time after onboarding -------------------------------------------------
    # These need a workspace token with the `config` scope (sopops Settings → Access) and SOPOPS_WORKSPACE, or an
    # onboarding token, which knows its own workspace. Saves are drafts: publishing stays with a person in sopops.

    KINDS = ("agents", "instructions", "procedures", "groups")

    def workspace() -> str:
        ws = _CALLER.get().workspace
        if ws:
            return ws
        try:
            return str(sopops.call("GET", "/v1/onboarding/session")["workspace"])
        except RuntimeError:
            how = "send the X-SopOps-Workspace header" if remote else "set SOPOPS_WORKSPACE"
            raise RuntimeError(f"{how} to the workspace id (the /w/<id>/ part of its URL)") from None

    def kind_path(kind: str, item_id: str | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        tail = f"/{quote(item_id, safe='')}" if item_id else ""
        return f"/v1/workspaces/{quote(workspace(), safe='')}/config/{kind}{tail}"

    @server.tool()
    @_explained
    def list_sops() -> dict[str, Any]:
        """Everything this workspace tells its agents right now: agents, shared instructions, procedures and groups,
        each with its id, name and version, and who uses it. Start here before changing anything."""
        got: dict[str, Any] = sopops.call("GET", f"/v1/workspaces/{quote(workspace(), safe='')}/config")
        def brief(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            keep = ("id", "name", "version", "used_by", "platform", "blocks")
            return [{k: v for k, v in it.items() if k in keep} for it in items]
        return {k: brief(got.get(k, [])) for k in KINDS}

    @server.tool()
    @_explained
    def get_sop(kind: str, id: str) -> dict[str, Any]:
        """One item in full, as its fields. `kind` is agents, instructions, procedures or groups."""
        return dict(sopops.call("GET", kind_path(kind, id)))

    @server.tool()
    @_explained
    def get_sop_settings() -> dict[str, Any]:
        """The workspace defaults that affect every compiled agent: placeholder values and the procedures heading.
        Read these before changing a shared block or an agent whose prompt uses default variables."""
        return dict(sopops.call("GET", f"/v1/workspaces/{quote(workspace(), safe='')}/config/settings"))

    @server.tool()
    @_explained
    def save_sop(kind: str, id: str, fields: dict[str, Any], note: str | None = None) -> dict[str, Any]:
        """Save an item as a draft, creating it if it's new. Send the whole item, not a patch: read it with
        `get_sop` first, change what you mean to, and send that back. sop checks it, so a save that wouldn't
        compile is refused with its issues.

        Nothing reaches a call until someone publishes a release in sopops; say so when you report back."""
        body = {**fields, "id": id}
        if note:
            body["note"] = note[:500]
        return dict(sopops.call("PUT", kind_path(kind, id), body))

    @server.tool()
    @_explained
    def save_sop_settings(fields: dict[str, Any], note: str | None = None) -> dict[str, Any]:
        """Save workspace-wide default variables or the procedures heading as a draft. Read the current settings
        first with `get_sop_settings`, then send the complete settings object with only the intended changes."""
        body = dict(fields)
        if note:
            body["note"] = note[:500]
        return dict(sopops.call("PUT", f"/v1/workspaces/{quote(workspace(), safe='')}/config/settings", body))

    @server.tool()
    @_explained
    def delete_sop(kind: str, id: str, note: str | None = None) -> dict[str, Any]:
        """Delete one agent, instruction, procedure or group as a draft. Before calling this, read the item and its
        `used_by` list, explain the effect to the user, and get an explicit request to delete it. Nothing goes live
        until a person publishes a release in sopops."""
        params = {"note": note[:500]} if note else None
        return dict(sopops.call("DELETE", kind_path(kind, id), params=params))

    @server.tool()
    @_explained
    def get_sop_history(kind: str, id: str) -> list[dict[str, Any]]:
        """Every saved version of one SOP, newest first. Use this to understand why it changed before editing or
        restoring an older version."""
        return list(sopops.call("GET", f"{kind_path(kind, id)}/history"))

    @server.tool()
    @_explained
    def get_sop_version(kind: str, id: str, version: int, file: str | None = None) -> dict[str, Any]:
        """One historical SOP version in full. For a procedure with more than one historical file, pass the `file`
        value returned by `get_sop_history`. To restore it, read it here and save its fields with `save_sop`."""
        params = {"file": file} if file else None
        return dict(sopops.call("GET", f"{kind_path(kind, id)}/versions/{version}", params=params))

    @server.tool()
    @_explained
    def preview_agent(agent: str) -> dict[str, Any]:
        """What one agent's full instructions look like at head, against what its calls run today: the compiled
        prompt, the blocks it's built from, and the live release's text. Saves nothing."""
        return dict(sopops.call("POST", f"{kind_path('agents', agent)}/preview", {}))

    @server.tool()
    @_explained
    def report_progress(step: str, message: str) -> dict[str, Any]:
        """Tell the person watching the sopops Import agents page what you're doing (one short sentence)."""
        if not sopops.connected:
            why = "no Authorization header" if remote else "no SOPOPS_URL/SOPOPS_TOKEN"
            return {"ok": False, "reason": f"not connected to sopops ({why}); progress is only shown in sopops"}
        sopops.call("POST", "/v1/onboarding/progress", {"step": step[:64], "message": message[:4000]})
        return {"ok": True}

    @server.tool()
    @_explained
    def report_pull_request(url: str) -> dict[str, Any]:
        """Repo mode: tell sopops the URL of the onboarding pull request you opened (adding the sop project). sopops
        follows it, and pulls the folder into drafts when the user merges it."""
        got = sopops.call("POST", "/v1/onboarding/pull-request", {"url": url})
        return {"pr_url": got["pr_url"], "pr_state": got["pr_state"], "next": "Tell the user the pull request is open for their review; don't merge it."}

    @server.tool()
    @_explained
    def open_pull_request(description: str = "") -> dict[str, Any]:
        """Repo mode, when the kickoff says sopops opens the pull request (a GitHub App connection): sopops writes your
        pushed proposal as a sop project at the connected folder, opens the onboarding pull request, and follows it.
        Push the proposal first. Don't run git or gh for it."""
        got = sopops.call("POST", "/v1/onboarding/pull-request/open", {"description": description or None})
        return {"pr_url": got["pr_url"], "pr_state": got["pr_state"], "next": "Tell the user the pull request is open for their review; don't merge it."}

    if remote:

        @_explained
        def sop_project_files(proposal: dict[str, Any], path: str = "sops") -> dict[str, Any]:
            """The proposal as a sop project (sop.yaml, instructions/, procedures/, agents/) under `path`, as sopops's
            sop writes and checks it. This sopops MCP server is remote and can't reach your repo, so it returns the files
            instead of writing them: write each one at its path (relative to the current directory), overwriting files
            with the same names and leaving other files alone."""
            got = sopops.call("POST", "/v1/onboarding/project", {"proposal": proposal})
            root = Path(path.strip() or "sops")
            files = {}
            for rel, text in sorted(got["files"].items()):
                dest = root / rel
                if root.is_absolute() or ".." in dest.parts:
                    raise ValueError(f"pass a folder inside the repo; sopops must send files inside it: {dest.as_posix()}")
                files[dest.as_posix()] = text
            return {"files": files, "issues": got["issues"], "sop_validate": got["validation"],
                    "next": "Write each file in `files` at its path, then run `sop validate` there if the sop CLI is installed."}

        server.tool(name="write_sop_project")(sop_project_files)
        return server

    @_explained
    def write_sop_project(proposal: dict[str, Any], path: str = "sops") -> dict[str, Any]:
        """Write the proposal as a sop project (sop.yaml, instructions/, procedures/, agents/) at `path`, relative to the
        current directory. sopops writes the files with sop and checks them. Existing files with the same names are
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
        return {"written": written, "issues": got["issues"], "sop_validate": got["validation"]}

    server.tool(name="write_sop_project")(write_sop_project)
    return server


def install_skill(root: Path, agents: list[str], url: str, transport: httpx.BaseTransport | None = None, names: tuple[str, ...] = SKILLS) -> list[Path]:
    """Copy sopops's skills (sopops-onboard and sopops-ab-tests, from the sopops server at `url`, so they match that
    server) into the repo at `root` for each agent (`claude`, `codex`). Returns the files written."""
    skills = []
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=30, transport=transport) as c:
            for name in names:
                res = c.get("/v1/onboarding/skill", params={"name": name})
                if res.status_code == 404 and name != ONBOARD_SKILL:  # an older server without this skill
                    continue
                if res.status_code >= 400:
                    raise RuntimeError(f"sopops answered {res.status_code} for the {name} skill")
                skills.append(res.json())
    except httpx.HTTPError as e:
        raise RuntimeError(f"couldn't reach sopops at {url}: {type(e).__name__}") from None
    out = []
    for skill in skills:
        for agent in agents:
            target = root / SKILL_DIRS[agent] / skill["name"] / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(skill["text"])
            out.append(target)
    return out


ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


def http_app(env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None, path: str = "/mcp", host: str = "0.0.0.0") -> ASGIApp:
    """The remote MCP server as an ASGI app: Streamable HTTP at `path`, and `GET /healthz` for a load balancer.

    Stateless (no MCP session to keep), so any instance can answer any request and a restart drops nothing. A request to
    `path` without `Authorization: Bearer <token>` gets 401 before it reaches MCP; the token itself is checked by the
    sopops API on each tool call. Responses stream (SSE) as the MCP library writes them: nothing here buffers them."""
    server = make_server(env, transport, remote=True)
    app = server.streamable_http_app(streamable_http_path=path, stateless_http=True, host=host)
    return _Gate(app, path)  # type: ignore[arg-type]


class _Gate:
    """Health check and the Authorization requirement in front of the MCP app, as plain ASGI so streams pass through."""

    def __init__(self, app: ASGIApp, path: str):
        self.app, self.path = app, path.rstrip("/") or "/"

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        if scope["type"] == "http":
            if scope["path"] == "/healthz":
                return await _respond(send, 200, {"ok": True})
            if (scope["path"].rstrip("/") or "/") == self.path and scope["method"] != "OPTIONS":
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
                if not bearer(headers.get("authorization")):
                    detail = "Send your sopops token (Settings → Tokens, with the config scope, or an onboarding token) as `Authorization: Bearer <token>`."
                    challenge = 'Bearer realm="sopops", error="invalid_request", error_description="a sopops token is required"'
                    return await _respond(send, 401, {"error": "unauthorized", "detail": detail}, [(b"www-authenticate", challenge.encode())])
        await self.app(scope, receive, send)


async def _respond(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, body: dict[str, Any], headers: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps(body).encode()
    head = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()), *(headers or [])]
    await send({"type": "http.response.start", "status": status, "headers": head})
    await send({"type": "http.response.body", "body": data})


def serve(host: str, port: int, path: str = "/mcp") -> None:
    """Run the remote MCP server with uvicorn. Its keep-alive outlasts a load balancer's idle timeout (60 s on an AWS
    ALB), so the balancer never reuses a connection uvicorn has just closed (a 502)."""
    import uvicorn

    if not os.environ.get("SOPOPS_URL"):
        raise SystemExit("sopops-mcp serve: set SOPOPS_URL to the sopops API the tools call")
    uvicorn.run(http_app(path=path, host=host), host=host, port=port, timeout_keep_alive=75, proxy_headers=True, forwarded_allow_ips="*", log_level="info")


def main(argv: list[str] | None = None) -> None:
    """`sopops-mcp`: the MCP server on stdio. `sopops-mcp serve [--host] [--port] [--path]`: the remote server over
    Streamable HTTP. `sopops-mcp install-skill [--claude] [--codex] [--url URL]`: copy the skills into this repo
    (.claude/skills, .agents/skills; both by default)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install-skill"]:
        parser = argparse.ArgumentParser(prog="sopops-mcp install-skill", description="Copy sopops's skills (onboarding, A/B tests) into this repo.")
        parser.add_argument("--claude", action="store_true", help="Claude Code: .claude/skills/<skill>/SKILL.md")
        parser.add_argument("--codex", action="store_true", help="Codex (and OpenCode): .agents/skills/<skill>/SKILL.md")
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
    if argv[:1] == ["serve"]:
        parser = argparse.ArgumentParser(prog="sopops-mcp serve", description="The sopops MCP server over Streamable HTTP, for many users: each request sends its own `Authorization: Bearer <sopops token>`. SOPOPS_URL is the sopops API it calls; SOPOPS_APP_URL is where people open sopops (for links).")
        parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Address to listen on (default: HOST, else 127.0.0.1).")
        parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Port (default: PORT, else 8000).")
        parser.add_argument("--path", default="/mcp", help="The MCP endpoint's path (default: /mcp).")
        args = parser.parse_args(argv[1:])
        serve(args.host, args.port, args.path)
        return
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    make_server().run("stdio")


if __name__ == "__main__":
    main()
