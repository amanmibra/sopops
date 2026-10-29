// Settings → MCP: the one command that connects a coding agent to this workspace, with a `config` token made here.
// The token is shown once, as in Settings → Access; later visits show its prefix and leave a placeholder in the command.
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { call, q, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { Banner, CopyBlock, ErrorBanner, Note, Panel } from "./bits";
import { SectionHead } from "./settings";
import { Button } from "./ui/button";
import { apiOrigin } from "@/lib/paths";

// sopops-mcp ships in the Python SDK's `mcp` extra; uvx installs it from the repo until it's on PyPI (see
// sdks/python/src/sopops/mcp.py and the server's kickoff).
const SOURCE = "sopops[mcp] @ git+https://github.com/sopops-dev/sopops@main#subdirectory=sdks/python";
const PLACEHOLDER = "<your token>";

function command(url: string, token: string, ws: string): string {
  return [
    "claude mcp add sopops \\",
    `  --env SOPOPS_URL=${url} \\`,
    `  --env SOPOPS_TOKEN=${token} \\`,
    `  --env SOPOPS_WORKSPACE=${ws} \\`,
    `  -- uvx --from "${SOURCE}" sopops-mcp`,
  ].join("\n");
}

export function McpSection() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const list = useQuery(q.tokens(ws));
  const [made, setMade] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const url = apiOrigin();
  const live = (list.data ?? []).filter((t) => t.active && t.scopes.includes("config"));
  const create = async () => {
    setBusy(true);
    try {
      const token = await call(sdk.createToken({ path: { workspace: ws }, body: { name: "Coding agent", scopes: ["config"], expires_in_days: null } }));
      setMade(token.token);
      await qc.invalidateQueries({ queryKey: ["ws", ws, "tokens"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <SectionHead title="Keep editing SOPs from your coding agent">
        Claude Code reads and edits this team's SOPs, and every change it makes is a draft until someone publishes it.
      </SectionHead>
      {list.isError && <ErrorBanner error={list.error} />}
      <Panel title="Run this once, in any terminal">
        {made ? (
          <Banner tone="ok">
            <b>Copy the command now. Its token isn't shown again.</b>
          </Banner>
        ) : live.length ? (
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Note>
              This team already has a token for coding agents ({live[0].prefix}…, made {when(live[0].created_at)}). Tokens are shown once, so paste yours in place of{" "}
              <code>{PLACEHOLDER}</code>.
            </Note>
            <Button size="sm" variant="outline" disabled={busy} onClick={create}>
              Make another token
            </Button>
          </div>
        ) : (
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Button size="sm" disabled={busy} onClick={create}>
              {busy ? "Making…" : "Make a token"}
            </Button>
            <Note>It reads and edits SOPs as drafts. It can't publish.</Note>
          </div>
        )}
        <CopyBlock label="Connect your coding agent" text={command(url, made ?? PLACEHOLDER, ws)} />
      </Panel>
      <Panel title="What your agent can do here">
        <ul className="grid list-disc gap-1 pl-5 text-sm">
          <li>
            <code>list_sops</code> lists what this team tells its agents right now.
          </li>
          <li>
            <code>get_sop</code> reads one agent, shared instruction, procedure or group in full.
          </li>
          <li>
            <code>save_sop</code> saves one back as a draft, and sop checks it.
          </li>
          <li>
            <code>preview_agent</code> shows an agent's full instructions, against what its calls run today.
          </li>
        </ul>
        <p className="mt-3 text-sm">Saves are drafts. Nothing reaches a live call until someone publishes a release.</p>
      </Panel>
    </>
  );
}
