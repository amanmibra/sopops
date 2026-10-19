// How calls come back to sopops with no code in the agent, inside Settings → Platforms: the LiveKit project and its
// webhook, the ElevenLabs post-call webhook and Enterprise live monitoring, and the Vapi server messages to turn on.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { toast } from "sonner";
import { type CaptureOut, call, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { useWs } from "@/lib/workspace";
import { Banner, ConfirmButton, ErrorBanner, Note } from "./bits";
import { PlatformIcon } from "./brand-icon";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const captureKey = (ws: string) => ["ws", ws, "runtime", "capture"];
const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied."));
const ANY = "*";

function useCapture() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: captureKey(ws), queryFn: () => call(sdk.getCapture({ path: { workspace: ws } })) });
  const refresh = () => qc.invalidateQueries({ queryKey: captureKey(ws) });
  const set = (data: CaptureOut) => qc.setQueryData(captureKey(ws), data);
  return { ws, q, refresh, set };
}

/** A webhook address to copy, with the steps that go with it. */
function HookBox({ title, path, steps, children }: { title: string; path: string; steps: ReactNode[]; children?: ReactNode }) {
  const url = `${window.location.origin}${path}`;
  return (
    <div className="bg-muted/40 grid gap-2 rounded-md border px-3 py-2.5 text-[13px]">
      <div className="font-medium">{title}</div>
      <div className="bg-card flex items-center gap-2 rounded-md border px-2.5 py-1">
        <code className="flex-1 font-mono text-[12px] break-all">{url}</code>
        <Button size="icon-sm" variant="ghost" aria-label={`Copy the ${title} address`} onClick={() => copy(url)}>
          <Copy />
        </Button>
      </div>
      <ol className="text-muted-foreground grid max-w-prose list-decimal gap-1 pl-5 leading-relaxed">
        {steps.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>
      {children}
    </div>
  );
}

function Received({ at }: { at: string | null | undefined }) {
  return <p className="text-muted-foreground text-xs">{at ? `Last received ${when(at)}.` : "Nothing received yet."}</p>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <span className="grid w-48 text-[13px]">
        {label} {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
      </span>
      {children}
    </div>
  );
}

// --- LiveKit ------------------------------------------------------------------------------------------

/** Connect a LiveKit Cloud project: sopops joins each call as a hidden participant and records it. */
export function LiveKitCapture() {
  const { ws, q, refresh } = useCapture();
  const lk = q.data?.livekit;
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setUrl(lk?.url ?? ""), [lk?.url]);

  const save = async () => {
    setBusy(true);
    try {
      await call(sdk.setLivekit({ path: { workspace: ws }, body: { url, api_key: key.trim() || null, api_secret: secret.trim() || null } }));
      setKey("");
      setSecret("");
      await refresh();
      toast.success("LiveKit project saved. Now add the webhook in LiveKit Cloud.");
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const test = async () => {
    setBusy(true);
    try {
      const r = await call(sdk.testLivekit({ path: { workspace: ws } }));
      (r.ok ? toast.success : toast.error)(r.message);
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const disconnect = async () => {
    try {
      await call(sdk.deleteLivekit({ path: { workspace: ws } }));
      await refresh();
      toast.success("LiveKit disconnected. sopops stops joining calls.");
    } catch (e) {
      toast.error(msg(e));
    }
  };

  const needKeys = !lk?.connected;
  return (
    <div className="border-t py-4 first-of-type:border-t-0">
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
        <PlatformIcon platform="livekit" size={16} /> LiveKit
      </h3>
      <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
        Connect your LiveKit Cloud project, and sopops joins each call as a hidden participant. It reads what the agent and the caller say as it happens,
        so your agent needs no changes. Tool calls aren't in the room, so they aren't recorded this way.
      </p>
      {q.isError && <ErrorBanner error={q.error} />}
      {lk && !lk.recorder_installed && (
        <Banner tone="warn">
          This server can't join LiveKit calls yet. Install it with <code>pip install 'sopops-server[livekit]'</code> where sopops and its worker run, then
          restart.
        </Banner>
      )}
      {lk && (
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Field label="Project URL" hint="From your project's settings">
            <Input className="max-w-80 flex-1" placeholder="wss://my-project.livekit.cloud" aria-label="LiveKit project URL" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <Field label="API key" hint={lk.api_key.set ? "Leave empty to keep the saved one" : "Settings, then API keys"}>
            <Input
              className="max-w-80 flex-1"
              autoComplete="off"
              placeholder={lk.api_key.set ? `Saved: ${lk.api_key.hint ?? ""}` : "API key"}
              aria-label="LiveKit API key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </Field>
          <Field label="API secret">
            <Input
              type="password"
              autoComplete="off"
              className="max-w-80 flex-1"
              placeholder={lk.api_secret.set ? "Saved" : "API secret"}
              aria-label="LiveKit API secret"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" type="submit" disabled={busy || !url.trim() || (needKeys && (!key.trim() || !secret.trim()))}>
              {lk.connected ? "Save" : "Connect"}
            </Button>
            {lk.connected && (
              <>
                <Button size="sm" variant="outline" type="button" disabled={busy} onClick={test}>
                  Test
                </Button>
                <span className="flex-1" />
                <ConfirmButton size="sm" variant="ghost" type="button" className="text-err" confirm="Click again: sopops stops joining calls" onConfirm={disconnect}>
                  Disconnect
                </ConfirmButton>
              </>
            )}
          </div>
        </form>
      )}
      {lk?.connected && (
        <div className="mt-3 grid gap-3">
          <HookBox
            title="LiveKit webhook"
            path={lk.webhook_path}
            steps={[
              <>
                In LiveKit Cloud, open <b>Settings</b>, then <b>Webhooks</b>, and add a webhook with this address.
              </>,
              <>For its signing key, pick the API key you added here.</>,
              <>Start a call. sopops joins when your agent does, and the call shows up in Sessions as it happens.</>,
            ]}
          >
            <Received at={lk.last_event_at} />
          </HookBox>
          <AgentMap />
        </div>
      )}
    </div>
  );
}

/** Which sopops agent a LiveKit call belongs to, when the call doesn't say. */
function AgentMap() {
  const { ws, q, refresh } = useCapture();
  const lk = q.data?.livekit;
  const agents = useQuery({ queryKey: ["ws", ws, "runtime", "delivery"], queryFn: () => call(sdk.listDelivery({ path: { workspace: ws } })) });
  const [rows, setRows] = useState<{ name: string; agent: string }[]>([]);
  const [fallback, setFallback] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const map = lk?.agent_map ?? {};
    setRows(Object.entries(map).filter(([name]) => name !== ANY).map(([name, agent]) => ({ name, agent })));
    setFallback(map[ANY] ?? "");
  }, [lk?.agent_map]);
  if (!lk) return null;

  const save = async () => {
    const agent_map: Record<string, string> = {};
    for (const r of rows) if (r.name.trim() && r.agent) agent_map[r.name.trim()] = r.agent;
    if (fallback) agent_map[ANY] = fallback;
    setBusy(true);
    try {
      await call(sdk.setLivekit({ path: { workspace: ws }, body: { url: lk.url ?? "", agent_map } }));
      await refresh();
      toast.success("Agent mapping saved.");
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const ids = (agents.data ?? []).map((a) => a.agent);
  const pick = (value: string, onChange: (v: string) => void, label: string, none?: string) => (
    <Select value={value || (none ? "-" : undefined)} onValueChange={(v) => onChange(v === "-" ? "" : v)}>
      <SelectTrigger size="sm" className="min-w-44" aria-label={label}>
        <SelectValue placeholder="Pick an agent" />
      </SelectTrigger>
      <SelectContent>
        {none && <SelectItem value="-">{none}</SelectItem>}
        {ids.map((id) => (
          <SelectItem key={id} value={id}>
            {id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  return (
    <div className="grid gap-2 rounded-md border px-3 py-2.5 text-[13px]">
      <div className="font-medium">Which agent each call belongs to</div>
      <ul className="text-muted-foreground grid max-w-prose list-disc gap-1 pl-5 leading-relaxed">
        <li>sopops looks for the agent name your LiveKit agent was dispatched with. If it matches a sopops agent, that's the one.</li>
        <li>
          If not, it reads <code>{'{"sopops": {"agent": "<id>"}}'}</code> from the job or room metadata.
        </li>
        <li>If neither works, it uses the list below.</li>
      </ul>
      {rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-56"
            placeholder="LiveKit agent name"
            aria-label="LiveKit agent name"
            value={r.name}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
          />
          <span className="text-muted-foreground">is</span>
          {pick(r.agent, (v) => setRows(rows.map((x, j) => (j === i ? { ...x, agent: v } : x))), `sopops agent for ${r.name || "this name"}`)}
          <Button size="icon-sm" variant="ghost" aria-label="Remove this row" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
            <X />
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground w-56">Any other call</span>
        {pick(fallback, setFallback, "sopops agent for any other call", "Keep the name as sent")}
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => setRows([...rows, { name: "", agent: "" }])}>
          <Plus /> Add a name
        </Button>
        <Button size="sm" disabled={busy} onClick={save}>
          Save mapping
        </Button>
      </div>
    </div>
  );
}

// --- ElevenLabs and Vapi: shown under each platform's keys ---------------------------------------------

/** How calls on this platform come back to sopops: below its API key and call-start webhook. */
export function SessionsBack({ platform }: { platform: "elevenlabs" | "vapi" }) {
  return platform === "elevenlabs" ? <ElevenLabsBack /> : <VapiBack />;
}

function ElevenLabsBack() {
  const { ws, q, refresh, set } = useCapture();
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const el = q.data?.elevenlabs;
  if (q.isError) return <ErrorBanner error={q.error} />;
  if (!el || !q.data) return null;

  const saveSecret = async () => {
    setBusy(true);
    try {
      await call(sdk.setPostCallSecret({ path: { workspace: ws }, body: { value: secret.trim() } }));
      setSecret("");
      await refresh();
      toast.success("Post-call secret saved.");
    } catch (e) {
      toast.error(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const removeSecret = async () => {
    try {
      await call(sdk.deletePostCallSecret({ path: { workspace: ws } }));
      await refresh();
    } catch (e) {
      toast.error(msg(e));
    }
  };
  const setLive = async (on: boolean) => {
    try {
      const out = await call(sdk.setElevenlabsLive({ path: { workspace: ws }, body: { on } }));
      set({ ...q.data!, elevenlabs: out });
      toast.success(on ? "sopops listens to calls as they happen, where ElevenLabs allows it." : "Live monitoring is off.");
    } catch (e) {
      toast.error(msg(e));
    }
  };

  return (
    <>
      <HookBox
        title="Post-call webhook"
        path={el.post_call_path}
        steps={[
          <>
            On ElevenLabs, open the Agents settings, and add a webhook with this address. Use it as the <b>Post-call webhook</b>, with transcripts on.
          </>,
          <>ElevenLabs shows a secret for it. Paste that secret below.</>,
        ]}
      >
        <Field label="Post-call secret" hint="Checks each call came from ElevenLabs">
          {el.post_call_secret.set ? (
            <>
              <code className="text-[13px]">{el.post_call_secret.hint}</code>
              <span className="flex-1" />
              <ConfirmButton size="sm" variant="ghost" className="text-err" confirm="Click again: calls stop arriving" onConfirm={removeSecret}>
                Remove
              </ConfirmButton>
            </>
          ) : (
            <form
              className="flex flex-1 flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (secret.trim()) saveSecret();
              }}
            >
              <Input
                type="password"
                autoComplete="off"
                className="max-w-80 flex-1"
                placeholder="The secret ElevenLabs shows"
                aria-label="ElevenLabs post-call secret"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
              <Button size="sm" type="submit" disabled={busy || !secret.trim()}>
                Save
              </Button>
            </form>
          )}
        </Field>
        <Received at={el.last_post_call_at} />
      </HookBox>
      <div className="grid gap-2 rounded-md border px-3 py-2.5 text-[13px]">
        <label className="flex items-center gap-2 font-medium">
          <Checkbox checked={el.live} onCheckedChange={(v) => setLive(v === true)} aria-label="Enterprise live monitoring" />
          Listen to calls as they happen (ElevenLabs Enterprise)
        </label>
        <p className="text-muted-foreground max-w-prose leading-relaxed">
          When the call-start webhook above serves a call, sopops listens to it live with your API key. Turn on <b>Monitoring</b> in the agent's
          Advanced settings on ElevenLabs. If live listening isn't available, calls still arrive from the post-call webhook when they end.
        </p>
        {el.live && !el.api_key_set && <Note>Add an ElevenLabs API key above. Live listening uses it.</Note>}
        {el.live && el.live_state === "on" && <p className="text-primary text-xs">Working. Last checked {when(el.live_checked_at)}.</p>}
        {el.live && el.live_state === "unavailable" && (
          <Banner tone="warn" className="mb-0">
            Not available, so calls arrive when they end. {el.live_detail}
            <span className="text-muted-foreground block text-xs">Checked {when(el.live_checked_at)}.</span>
          </Banner>
        )}
        {el.live && !el.live_state && <p className="text-muted-foreground text-xs">Waiting for the next call to check it.</p>}
      </div>
    </>
  );
}

function VapiBack() {
  const { q } = useCapture();
  if (q.isError) return <ErrorBanner error={q.error} />;
  const v = q.data?.vapi;
  if (!v) return null;
  const why: Record<string, string> = {
    "assistant-request": "gets the instructions when a call starts",
    "end-of-call-report": "sends each call when it ends",
    "conversation-update": "sends each turn as it happens",
    transcript: "sends what's said as it happens",
  };
  return (
    <div className="grid gap-2 rounded-md border px-3 py-2.5 text-[13px]">
      <div className="font-medium">Calls back to sopops</div>
      <p className="text-muted-foreground max-w-prose leading-relaxed">
        Vapi sends calls to the same Server URL. On Vapi, under the assistant's <b>Server Messages</b>, turn these on:
      </p>
      <ul className="text-muted-foreground grid max-w-prose list-disc gap-1 pl-5 leading-relaxed">
        {v.server_messages.map((m) => (
          <li key={m}>
            <code>{m}</code>: {why[m] ?? ""}
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground text-xs">Tool calls come with the end-of-call report.</p>
    </div>
  );
}
