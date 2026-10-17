// Runtime delivery: each agent's delivery mode and the platform credentials (Settings), and push results (a release).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, PlugZap, RefreshCw, Send } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { type AgentDeliveryOut, type PlatformCredentials, type PushResultOut, call, sdk } from "@/lib/api";
import { platformName, when } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

type Mode = AgentDeliveryOut["mode"];
type Platform = PlatformCredentials["platform"];
type SecretKind = "api-key" | "webhook-secret";

const MODE: Record<Mode, string> = {
  pull: "Pull at call start",
  push: "Push on publish",
  both: "Push and pull",
};

const WEBHOOK: Record<Platform, { path: string; how: string }> = {
  elevenlabs: {
    path: "runtime/elevenlabs/conversation-init",
    how: "ElevenLabs: Agents settings → Conversation initiation webhook. Send the secret as the header X-Sopops-Secret (from a workspace secret), and allow the agent to override its system prompt (Security → Overrides).",
  },
  vapi: {
    path: "runtime/vapi/assistant-request",
    how: "Vapi: the phone number's Server URL, with a Bearer Token credential set to the secret (or the X-Vapi-Secret header, or an HMAC credential).",
  },
};

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const keyOf = (ws: string, what: string) => ["ws", ws, "runtime", what];

// --- Settings: delivery and platforms ---------------------------------------------------------------

export function RuntimePanels() {
  return (
    <>
      <DeliveryPanel />
      <PlatformsPanel />
    </>
  );
}

function DeliveryPanel() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const rows = useQuery({ queryKey: keyOf(ws, "delivery"), queryFn: () => call(sdk.listDelivery({ path: { workspace: ws } })) });
  const set = async (agent: string, mode: Mode) => {
    try {
      await call(sdk.setDelivery({ path: { workspace: ws, agent }, body: { mode } }));
      await qc.invalidateQueries({ queryKey: keyOf(ws, "delivery") });
      toast.success(mode === "pull" ? `${agent} fetches its prompt at call start.` : `${agent} is updated on its platform at every publish.`);
    } catch (e) {
      toast.error(msg(e));
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Send className="size-4" /> Delivery
        </span>
      }
      className="mt-8"
    >
      <p className="text-muted-foreground mb-3 text-[13px]">
        How each agent gets its prompt. <b>Pull</b>: the agent (or its platform's call-start webhook) fetches the live release when a call starts.{" "}
        <b>Push</b>: publishing, or making a release live, writes the prompt into the agent on ElevenLabs or Vapi. LiveKit agents pull.
      </p>
      {rows.isError && <ErrorBanner error={rows.error} />}
      {rows.data &&
        (rows.data.length ? (
          <table className="w-full border-collapse">
            <tbody>
              {rows.data.map((d) => (
                <tr key={d.agent}>
                  <td className="border-b px-2.5 py-2">
                    <b>{d.agent}</b>
                  </td>
                  <td className="text-muted-foreground border-b px-2.5 py-2 text-[13px]">
                    {platformName(d.platform)}: {d.platform_id}
                  </td>
                  <td className="border-b px-2.5 py-2 text-right">
                    {d.modes.length > 1 ? (
                      <Select value={d.mode} onValueChange={(v) => set(d.agent, v as Mode)}>
                        <SelectTrigger size="sm" className="ml-auto min-w-44" aria-label={`Delivery for ${d.agent}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {d.modes.map((m) => (
                            <SelectItem key={m} value={m}>
                              {MODE[m]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <span className="text-muted-foreground text-[13px]">{MODE[d.mode]}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Note>No agents yet.</Note>
        ))}
    </Panel>
  );
}

function PlatformsPanel() {
  const { ws } = useWs();
  const creds = useQuery({ queryKey: keyOf(ws, "platforms"), queryFn: () => call(sdk.listPlatforms({ path: { workspace: ws } })) });
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <PlugZap className="size-4" /> Platforms
        </span>
      }
    >
      <p className="text-muted-foreground mb-3 text-[13px]">
        An API key lets sopops push prompts to the platform; a webhook secret lets the platform fetch them at call start. Both are stored encrypted
        and never shown again.
      </p>
      {creds.isError && <ErrorBanner error={creds.error} />}
      {creds.data?.map((c) => <PlatformRow key={c.platform} creds={c} />)}
    </Panel>
  );
}

function PlatformRow({ creds }: { creds: PlatformCredentials }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = platformName(creds.platform);
  const hook = `${window.location.origin}/v1/workspaces/${encodeURIComponent(ws)}/${WEBHOOK[creds.platform].path}`;
  const refresh = () => qc.invalidateQueries({ queryKey: keyOf(ws, "platforms") });

  const save = async (kind: SecretKind, value: string | null) => {
    setBusy(true);
    try {
      const made = await call(sdk.setPlatformSecret({ path: { workspace: ws, platform: creds.platform, kind }, body: { value } }));
      if (made.secret) setShown(made.secret);
      setKey("");
      await refresh();
      toast.success(kind === "api-key" ? `${name} API key saved.` : `${name} webhook secret saved.`);
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (kind: SecretKind) => {
    try {
      await call(sdk.deletePlatformSecret({ path: { workspace: ws, platform: creds.platform, kind } }));
      if (kind === "webhook-secret") setShown(null);
      await refresh();
    } catch (e) {
      toast.error(msg(e));
    }
  };
  const test = async () => {
    setBusy(true);
    try {
      const r = await call(sdk.testPlatformKey({ path: { workspace: ws, platform: creds.platform }, body: {} }));
      (r.ok ? toast.success : toast.error)(r.message);
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied."));

  return (
    <div className="border-t py-3 first-of-type:border-t-0">
      <h3 className="mb-2 text-sm font-semibold">{name}</h3>
      <div className="grid gap-2.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="w-32 text-[13px]">API key</span>
          {creds.api_key.set ? (
            <>
              <code className="text-[13px]">{creds.api_key.hint}</code>
              <span className="text-muted-foreground text-[13px]">saved {creds.api_key.created_at ? when(creds.api_key.created_at) : ""}</span>
              <span className="flex-1" />
              <Button size="sm" variant="outline" disabled={busy} onClick={test}>
                Test
              </Button>
              <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again: pushes stop" onConfirm={() => remove("api-key")}>
                Remove
              </ConfirmButton>
            </>
          ) : (
            <form
              className="flex flex-1 flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (key.trim()) save("api-key", key.trim());
              }}
            >
              <Input
                type="password"
                autoComplete="off"
                className="max-w-80 flex-1"
                placeholder={`${name} API key`}
                aria-label={`${name} API key`}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <Button size="sm" type="submit" disabled={busy || !key.trim()}>
                Save
              </Button>
            </form>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="w-32 text-[13px]">Webhook secret</span>
          {creds.webhook_secret.set ? (
            <>
              <code className="text-[13px]">{creds.webhook_secret.hint}</code>
              <span className="flex-1" />
              <ConfirmButton size="sm" variant="outline" disabled={busy} confirm="Click again: the old secret stops working" onConfirm={() => save("webhook-secret", null)}>
                Replace
              </ConfirmButton>
              <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again to remove" onConfirm={() => remove("webhook-secret")}>
                Remove
              </ConfirmButton>
            </>
          ) : (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => save("webhook-secret", null)}>
              Make a webhook secret
            </Button>
          )}
        </div>
        {shown && (
          <Banner tone="ok">
            <b>Copy this secret now. It isn't shown again.</b>
            <div className="bg-card mt-2 flex items-center gap-2 rounded-md border px-2.5 py-1.5">
              <code className="flex-1 font-mono text-[13px] break-all">{shown}</code>
              <Button size="icon-sm" variant="ghost" aria-label="Copy secret" onClick={() => copy(shown)}>
                <Copy />
              </Button>
            </div>
          </Banner>
        )}
        <p className="text-muted-foreground text-[13px]">
          Call-start webhook: <code className="break-all">{hook}</code>. {WEBHOOK[creds.platform].how}
        </p>
      </div>
    </div>
  );
}

// --- a release: push results -----------------------------------------------------------------------------

const STATUS: Record<PushResultOut["status"], { label: string; className: string }> = {
  ok: { label: "pushed", className: "bg-brand-soft text-primary border-transparent" },
  unchanged: { label: "already live", className: "" },
  error: { label: "failed", className: "bg-err-soft text-err border-transparent" },
};

export function PushResultsPanel({ number, live }: { number: number; live: boolean }) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const key = keyOf(ws, `pushes-${number}`);
  // pushes run after the publish responds, so a live release is checked again for a little while
  const pushes = useQuery({
    queryKey: key,
    queryFn: () => call(sdk.listPushes({ path: { workspace: ws, number } })),
    refetchInterval: (q) => (live && (q.state.dataUpdateCount ?? 0) < 6 ? 4000 : false),
  });
  if (pushes.isError) return <ErrorBanner error={pushes.error} />;
  if (!pushes.data?.length) return null;
  const failed = pushes.data.some((p) => p.status === "error");
  const retry = async (agents?: string[]) => {
    setBusy(true);
    try {
      const out = await call(sdk.retryPushes({ path: { workspace: ws, number }, body: { agents: agents ?? null } }));
      qc.setQueryData(key, out);
      const still = out.filter((p) => p.status === "error").length;
      (still ? toast.error : toast.success)(still ? `${still} push${still === 1 ? "" : "es"} still failing.` : "Pushed.");
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Pushed to platforms
          <span className="flex-1" />
          {failed && live && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => retry()}>
              <RefreshCw /> Retry failed
            </Button>
          )}
        </span>
      }
    >
      <table className="w-full border-collapse">
        <tbody>
          {pushes.data.map((p) => (
            <tr key={p.agent}>
              <td className="border-b px-2.5 py-2 align-top">
                <b>{p.agent}</b>
              </td>
              <td className="text-muted-foreground border-b px-2.5 py-2 align-top text-[13px]">
                {platformName(p.platform)}: {p.platform_id}
              </td>
              <td className="border-b px-2.5 py-2 align-top">
                <Tag className={STATUS[p.status].className}>{STATUS[p.status].label}</Tag>
                {p.error && <div className="text-err mt-1 text-[13px]">{p.error}</div>}
              </td>
              <td className="text-muted-foreground border-b px-2.5 py-2 align-top text-[13px]">
                {p.version_id && <div>version {p.version_id}</div>}
                {when(p.at)}
                {p.attempts > 1 ? `, attempt ${p.attempts}` : ""}
              </td>
              <td className="border-b px-2.5 py-2 text-right align-top">
                {live && (
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => retry([p.agent])}>
                    Push again
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!live && <Note>Only the live release is pushed. Make this release live to push it again.</Note>}
    </Panel>
  );
}
