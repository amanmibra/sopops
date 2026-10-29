// Settings → Tokens: workspace API tokens with scopes (ingest, runtime, config). Onboarding tokens come from Import agents.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type TokenOut, call, q, sdk } from "@/lib/api";
import { platformName, when } from "@/lib/kinds";
import { usePlatforms } from "@/lib/platforms";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { apiOrigin } from "@/lib/paths";

type Scope = "ingest" | "runtime" | "config";

const SCOPES: { scope: Scope; label: string; help: string }[] = [
  { scope: "ingest", label: "ingest", help: "send sessions" },
  { scope: "runtime", label: "runtime", help: "fetch instructions, report served calls, call-start webhooks" },
  { scope: "config", label: "config", help: "read and edit agents, shared instructions, procedures and groups as drafts; it can't publish" },
];

export function TokensPanel() {
  const { ws } = useWs();
  const plats = usePlatforms();
  const qc = useQueryClient();
  const list = useQuery(q.tokens(ws));
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Scope[]>(["ingest", "runtime"]);
  const [days, setDays] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `${apiOrigin()}/v1/workspaces/${encodeURIComponent(ws)}`;
  const refresh = () => qc.invalidateQueries({ queryKey: ["ws", ws, "tokens"] });

  const toggle = (s: Scope, on: boolean) => setScopes((cur) => (on ? [...new Set([...cur, s])] : cur.filter((x) => x !== s)));
  const create = async () => {
    setBusy(true);
    try {
      const n = Number.parseInt(days, 10);
      const made = await call(
        sdk.createToken({ path: { workspace: ws }, body: { name: name.trim(), scopes, expires_in_days: Number.isFinite(n) && n > 0 ? n : null } }),
      );
      setShown(made.token);
      setName("");
      setDays("");
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const revoke = async (t: TokenOut) => {
    try {
      await call(sdk.revokeToken({ path: { workspace: ws, id: t.id } }));
      await refresh();
      toast.success("Token revoked. It stops working now.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied."));
  const tokens = list.data ?? [];

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <KeyRound className="size-4" /> Tokens
        </span>
      }
    >
      <div className="text-muted-foreground mb-4 grid gap-2 text-sm leading-relaxed">
        <p>
          Send a token as <code>Authorization: Bearer &lt;token&gt;</code>. Its scopes decide what it can do.
        </p>
        <ul className="grid list-disc gap-1 pl-5">
          <li>
            <b className="text-foreground">ingest</b> sends sessions to <code className="break-all">{url}/sessions</code>.
          </li>
          <li>
            <b className="text-foreground">runtime</b> fetches instructions (<code>…/agents/&lt;agent&gt;</code>) and answers call-start webhooks. Once your
            team has a runtime token, every fetch needs one.
          </li>
        </ul>
        <p>Tokens for coding agents are made on the Import agents page.</p>
      </div>
      {list.isError && <ErrorBanner error={list.error} />}
      {shown && (
        <Banner tone="ok">
          <b>Copy this token now. It isn't shown again.</b>
          <div className="bg-card mt-2 flex items-center gap-2 rounded-md border px-2 py-2">
            <code className="flex-1 font-mono text-sm break-all">{shown}</code>
            <Button size="icon-sm" variant="ghost" aria-label="Copy token" onClick={() => copy(shown)}>
              <Copy />
            </Button>
          </div>
          <pre className="mt-2 overflow-x-auto text-xs">{`export SOPOPS_URL=${url}\nexport SOPOPS_TOKEN=${shown}`}</pre>
          <p className="mt-2 text-sm">For an agent on LiveKit Cloud, add these two as the agent's secrets instead (Agents, then your agent, then Secrets), and deploy it again.</p>
        </Banner>
      )}
      {tokens.length ? (
        <table className="mb-4 w-full text-sm">
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id} className={t.active ? "" : "text-muted-foreground"}>
                <td className="border-b py-2 pr-2">
                  <code>{t.prefix}…</code> {t.name && <span>{t.name}</span>}
                </td>
                <td className="border-b py-2 pr-2">
                  <span className="flex flex-wrap gap-1">
                    {t.scopes.map((s) => (
                      <Tag key={s}>{s}</Tag>
                    ))}
                  </span>
                </td>
                <td className="border-b py-2 pr-2">
                  made {when(t.created_at)}
                  {t.revoked_at ? `, revoked ${when(t.revoked_at)}` : t.expires_at ? `, ${t.active ? "expires" : "expired"} ${when(t.expires_at)}` : ""}
                </td>
                <td className="border-b py-2 text-right">
                  {t.active && (
                    <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again: it stops working" onConfirm={() => revoke(t)}>
                      Revoke
                    </ConfirmButton>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        list.isSuccess && <Note>No tokens yet. Anyone can fetch instructions, and sessions are refused until you make one.</Note>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="tok-name" className="text-sm">
            Name
          </Label>
          <Input id="tok-name" className="h-8 w-[200px]" placeholder={`e.g. ${platformName(plats.first)} agents`} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <fieldset className="flex flex-wrap gap-3 pb-2">
          <legend className="sr-only">Scopes</legend>
          {SCOPES.map(({ scope, label, help }) => (
            <span key={scope} className="flex items-center gap-2 text-sm" title={help}>
              <Checkbox id={`tok-${scope}`} checked={scopes.includes(scope)} onCheckedChange={(v) => toggle(scope, v === true)} />
              <Label htmlFor={`tok-${scope}`} className="text-sm font-normal">
                {label}
              </Label>
            </span>
          ))}
        </fieldset>
        <div className="grid gap-1">
          <Label htmlFor="tok-days" className="text-sm">
            Expires in (days)
          </Label>
          <Input id="tok-days" className="h-8 w-[110px]" inputMode="numeric" placeholder="never" value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} />
        </div>
        <Button size="sm" disabled={busy || !scopes.length} onClick={create}>
          Create token
        </Button>
      </div>
    </Panel>
  );
}
