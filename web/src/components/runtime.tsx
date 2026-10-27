// Runtime delivery: each platform's card (delivery mode, credentials, capture) in Settings, and push results (a release).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cable, Copy, RefreshCw } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { type PlatformCredentials, type PushResultOut, call, sdk } from "@/lib/api";
import { platformName, when } from "@/lib/kinds";
import { usePlatforms } from "@/lib/platforms";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note, Panel, Tag } from "./bits";
import { PlatformIcon, PlatformLabel } from "./brand-icon";
import { Backfill } from "./backfill";
import { LiveKitCapture, SessionsBack } from "./capture";
import { ConnectGuide } from "./connect-guide";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { apiOrigin } from "@/lib/paths";

type Mode = PlatformCredentials["delivery"];
type Platform = PlatformCredentials["platform"];
type SecretKind = "api-key" | "webhook-secret";

const MODE: Record<Mode, string> = {
  pull: "Pull at call start",
  push: "Push on publish",
  both: "Push and pull",
};

const WEBHOOK: Record<Platform, { path: string; steps: ReactNode[] }> = {
  elevenlabs: {
    path: "runtime/elevenlabs/conversation-init",
    steps: [
      <>In the agent's settings on ElevenLabs, set <b>Conversation initiation webhook</b> to this address.</>,
      <>Add the header <code>X-SopOps-Secret</code> with the webhook secret. Save it as a workspace secret on ElevenLabs first.</>,
      <>Under <b>Security → Overrides</b>, turn on <b>System prompt</b> (ElevenLabs' name for the agent's instructions) so sopops can set it.</>,
    ],
  },
  vapi: {
    path: "runtime/vapi/assistant-request",
    steps: [
      <>On Vapi, set the phone number's <b>Server URL</b> to this address.</>,
      <>Add a <b>Bearer Token</b> credential with the webhook secret. The <code>X-Vapi-Secret</code> header or an HMAC credential also work.</>,
    ],
  },
};

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const keyOf = (ws: string, what: string) => ["ws", ws, "runtime", what];

// --- Settings: platforms ---------------------------------------------------------------

export function RuntimePanels() {
  return (
    <>
      <PlatformsPanels />
      <Panel
        title={
          <span className="flex items-center gap-2">
            <Cable className="size-4" /> What each platform needs
          </span>
        }
      >
        <ConnectGuide />
      </Panel>
    </>
  );
}

/** One card per platform the team uses, and per other platform that has something saved already: its keys and capture. */
function PlatformsPanels() {
  const { ws } = useWs();
  const plats = usePlatforms();
  const creds = useQuery({ queryKey: keyOf(ws, "platforms"), queryFn: () => call(sdk.listPlatforms({ path: { workspace: ws } })) });
  const capture = useQuery({ queryKey: keyOf(ws, "capture"), queryFn: () => call(sdk.getCapture({ path: { workspace: ws } })) });
  const livekit = plats.has("livekit") || Boolean(capture.data?.livekit.connected);
  const saved = (p: Platform) => (p === "elevenlabs" ? Boolean(capture.data?.elevenlabs.post_call_secret.set || capture.data?.elevenlabs.last_post_call_at) : false);
  const rows = (creds.data ?? []).filter((c) => plats.has(c.platform) || c.api_key.set || c.webhook_secret.set || saved(c.platform));
  return (
    <>
      {creds.isError && <ErrorBanner error={creds.error} />}
      {livekit && (
        <PlatformPanel platform="livekit">
          <div className="text-muted-foreground mb-4 flex flex-wrap items-center gap-2 text-sm opacity-70">
            <span className="w-48">Delivery</span>
            <span>Always pulled at call start, by <code>attach</code> in your agent. LiveKit has nothing to push to.</span>
          </div>
          <LiveKitCapture />
        </PlatformPanel>
      )}
      {rows.map((c) => (
        <PlatformPanel key={c.platform} platform={c.platform}>
          <PlatformRow creds={c} />
        </PlatformPanel>
      ))}
    </>
  );
}

function PlatformPanel({ platform, children }: { platform: Platform | "livekit"; children: ReactNode }) {
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <PlatformIcon platform={platform} size={16} /> {platformName(platform)}
        </span>
      }
    >
      {children}
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
  const hook = `${apiOrigin()}/v1/workspaces/${encodeURIComponent(ws)}/${WEBHOOK[creds.platform].path}`;
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
  const deliver = async (mode: Mode) => {
    try {
      await call(sdk.setPlatformDelivery({ path: { workspace: ws, platform: creds.platform }, body: { mode } }));
      await refresh();
      toast.success(mode === "pull" ? `${name} agents fetch their instructions at call start.` : `${name} agents are updated at every publish.`);
    } catch (e) {
      toast.error(msg(e));
    }
  };

  return (
    <div>
      <p className="text-muted-foreground mb-3 max-w-prose text-sm leading-relaxed">
        Keys are stored encrypted, and a webhook secret is shown only once, when it's made.
      </p>
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="grid w-48 text-sm">
            Delivery <span className="text-muted-foreground text-xs">For every {name} agent</span>
          </span>
          <Select value={creds.delivery} onValueChange={(v) => void deliver(v as Mode)}>
            <SelectTrigger size="sm" className="min-w-44" aria-label={`${name} delivery`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(MODE) as Mode[]).map((m) => (
                <SelectItem key={m} value={m}>
                  {MODE[m]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {creds.delivery !== "pull" && !creds.api_key.set && <span className="text-err text-sm">Pushing needs the API key below.</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="grid w-48 text-sm">
            API key <span className="text-muted-foreground text-xs">Pushes instructions on publish</span>
          </span>
          {creds.api_key.set ? (
            <>
              <code className="text-sm">{creds.api_key.hint}</code>
              <span className="text-muted-foreground text-sm">saved {creds.api_key.created_at ? when(creds.api_key.created_at) : ""}</span>
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
        <div className="flex flex-wrap items-center gap-2">
          <span className="grid w-48 text-sm">
            Webhook secret <span className="text-muted-foreground text-xs">Fetches instructions at call start</span>
          </span>
          {creds.webhook_secret.set ? (
            <>
              <code className="text-sm">{creds.webhook_secret.hint}</code>
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
            <div className="bg-card mt-2 flex items-center gap-2 rounded-md border px-2 py-2">
              <code className="flex-1 font-mono text-sm break-all">{shown}</code>
              <Button size="icon-sm" variant="ghost" aria-label="Copy secret" onClick={() => copy(shown)}>
                <Copy />
              </Button>
            </div>
          </Banner>
        )}
        <div className="bg-muted/40 grid gap-2 rounded-md border px-3 py-2 text-sm">
          <div className="font-medium">Call-start webhook</div>
          <div className="bg-card flex items-center gap-2 rounded-md border px-2 py-1">
            <code className="flex-1 font-mono text-sm break-all">{hook}</code>
            <Button size="icon-sm" variant="ghost" aria-label="Copy webhook address" onClick={() => copy(hook)}>
              <Copy />
            </Button>
          </div>
          <ol className="text-muted-foreground grid max-w-prose list-decimal gap-1 pl-5 leading-relaxed">
            {WEBHOOK[creds.platform].steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
        <SessionsBack platform={creds.platform} />
        {creds.api_key.set && <Backfill platform={creds.platform} />}
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
              <td className="border-b px-2 py-2 align-top">
                <b>{p.agent}</b>
              </td>
              <td className="text-muted-foreground border-b px-2 py-2 align-top text-sm">
                <PlatformLabel platform={p.platform} />: {p.platform_id}
              </td>
              <td className="border-b px-2 py-2 align-top">
                <Tag className={STATUS[p.status].className}>{STATUS[p.status].label}</Tag>
                {p.error && <div className="text-err mt-1 text-sm">{p.error}</div>}
              </td>
              <td className="text-muted-foreground border-b px-2 py-2 align-top text-sm">
                {p.version_id && <div>version {p.version_id}</div>}
                {when(p.at)}
                {p.attempts > 1 ? `, attempt ${p.attempts}` : ""}
              </td>
              <td className="border-b px-2 py-2 text-right align-top">
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
