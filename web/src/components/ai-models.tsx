// A team's AI keys and models, in setup and in Settings under Checks: the TypeSafe key for Jev, a provider and its key,
// and the two models. Each is the team's own or the deployment's default; keys go in and never come back out.
// A hosted sopops (one with its model gateway) first asks: sopops's own models, or the team's own keys.
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useDeferredValue, useEffect, useState } from "react";
import { toast } from "sonner";
import { type AiSettingsOut, type ModelChoices, type ProviderIn, call, q, sdk } from "@/lib/api";
import { useWs } from "@/lib/workspace";
import { Banner, Segmented, Tag } from "./bits";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { PROVIDER_LOGO } from "./provider-logos";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

type ProviderId = ProviderIn["provider"];
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function useAISettings() {
  const { ws } = useWs();
  return useQuery(q.ai(ws));
}

function Source({ source, hint }: { source: "team" | "default" | null | undefined; hint?: string | null }) {
  if (source === "team") return <Tag tone="live">Your key {hint}</Tag>;
  if (source === "default") return <Tag>Default</Tag>;
  return <Tag tone="changed">Not set</Tag>;
}

/** The whole form, in setup and in Settings. */
export function AIModelsForm() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const res = useAISettings();
  const d = res.data;
  const refresh = async (next?: AiSettingsOut) => {
    if (next) qc.setQueryData(q.ai(ws).queryKey, next);
    await qc.invalidateQueries({ queryKey: ["ws", ws] });
  };
  if (!d) return <div className="bg-muted h-40 animate-pulse rounded-lg" />;
  if (!d.providers.some((p) => p.id === "sopops")) return <OwnKeys d={d} ws={ws} refresh={refresh} />;
  return <Hosted d={d} ws={ws} refresh={refresh} />;
}

function OwnKeys(props: SectionProps) {
  return (
    <div className="grid gap-5">
      <TypeSafeSection {...props} />
      <ProviderSection {...props} />
    </div>
  );
}

const SOPOPS_MODELS = [
  ["sopops-decision", "Decides which steps apply to a call and whether your agent followed each one."],
  ["sopops-fast", "Handles quick jobs, like grouping a new mistake with others like it."],
  ["sopops-reasoning", "Takes a second look at flagged calls, names each kind of mistake and drafts fixes to your agent's instructions."],
] as const;

/** A hosted sopops: its own models, ready to go, or the team's own keys. */
function Hosted(props: SectionProps) {
  const { d, ws, refresh } = props;
  const onSopops = d.provider.id === "sopops" && d.decision.model != null;
  const [mode, setMode] = useState<"sopops" | "own">(onSopops || d.provider.source !== "team" ? "sopops" : "own");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const useSopops = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(sdk.setAiProvider({ path: { workspace: ws }, body: { provider: "sopops", key: null, api_base: null } }));
      if (d.typesafe.source === "team") await call(sdk.resetAiSetting({ path: { workspace: ws, what: "typesafe" } }));
      await refresh();
      toast.success("Using sopops models");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  // The team's own keys, without sopops's models showing as their defaults.
  const own: AiSettingsOut = {
    ...d,
    providers: d.providers.filter((p) => p.id !== "sopops"),
    ...(d.provider.id === "sopops" && {
      provider: { id: null, source: null, hint: null, api_base: null },
      fast: { model: null, source: null },
      reasoning: { model: null, source: null },
    }),
  };
  return (
    <div className="grid gap-5">
      <Segmented
        label="Models"
        value={mode}
        options={[
          ["sopops", <>{PROVIDER_LOGO.sopops}sopops models</>],
          ["own", "Your own keys"],
        ]}
        onChange={(m) => setMode(m as "sopops" | "own")}
      />
      {mode === "sopops" ? (
        <section className="grid gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">sopops models</h3>
            {onSopops ? <Tag tone="live">In use</Tag> : <Tag>Recommended</Tag>}
          </div>
          <p className="text-muted-foreground -mt-1 text-sm leading-relaxed">
            sopops runs these models for you. There's nothing to set up, and your plan covers what they cost.
          </p>
          <ul className="grid gap-2">
            {SOPOPS_MODELS.map(([name, what]) => (
              <li key={name} className="grid gap-0.5 rounded-md border px-3 py-2">
                <span className="font-mono text-xs font-medium">{name}</span>
                <span className="text-muted-foreground text-sm">{what}</span>
              </li>
            ))}
          </ul>
          {error && (
            <Banner tone="err" className="mb-0">
              {error}
            </Banner>
          )}
          {!onSopops && (
            <div>
              <Button type="button" disabled={busy} onClick={() => void useSopops()}>
                {busy ? "Switching…" : "Use sopops models"}
              </Button>
            </div>
          )}
        </section>
      ) : (
        <OwnKeys d={own} ws={ws} refresh={refresh} />
      )}
    </div>
  );
}

type SectionProps = { d: AiSettingsOut; ws: string; refresh: (next?: AiSettingsOut) => Promise<void> };

function TypeSafeSection({ d, ws, refresh }: SectionProps) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await refresh(await call(sdk.setTypesafeKey({ path: { workspace: ws }, body: { key } })));
      setKey("");
      toast.success("TypeSafe key saved");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const useDefault = async () => refresh(await call(sdk.resetAiSetting({ path: { workspace: ws, what: "typesafe" } })));
  return (
    <section className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">TypeSafe key</h3>
        <Source source={d.typesafe.source} hint={d.typesafe.hint} />
      </div>
      <p className="text-muted-foreground text-sm leading-relaxed">
        Jev, a model from TypeSafe, decides which steps apply to a call and whether your agent followed each one. Checks don't run without it.{" "}
        <a className="font-medium underline underline-offset-2" href={d.typesafe_key_url} target="_blank" rel="noreferrer">
          Get a key
        </a>
      </p>
      <div className="flex flex-wrap gap-2">
        <Input
          id="typesafe-key"
          type="password"
          autoComplete="off"
          className="min-w-0 flex-1 basis-60"
          placeholder={d.typesafe.source ? "Paste a key to replace it" : "Paste your TypeSafe key"}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <Button type="button" variant="outline" disabled={!key.trim() || busy} onClick={() => void save()}>
          {busy ? "Checking…" : "Save key"}
        </Button>
        {d.typesafe.source === "team" && (
          <Button type="button" variant="ghost" className="text-muted-foreground" onClick={() => void useDefault()}>
            Remove
          </Button>
        )}
      </div>
      {error && (
        <Banner tone="err" className="mb-0">
          {error}
        </Banner>
      )}
    </section>
  );
}

function ProviderSection({ d, ws, refresh }: SectionProps) {
  const current = d.provider.id && d.provider.id !== "sopops" ? (d.provider.id as ProviderId) : "anthropic";
  const [provider, setProvider] = useState<ProviderId>(current);
  const [key, setKey] = useState("");
  const [base, setBase] = useState(d.provider.api_base ?? "");
  const [choices, setChoices] = useState<ModelChoices | null>(null);
  const [fast, setFast] = useState(d.fast.model ?? "");
  const [reasoning, setReasoning] = useState(d.reasoning.model ?? "");
  const [busy, setBusy] = useState<"key" | "models" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const option = d.providers.find((p) => p.id === provider)!;
  const sameProvider = provider === d.provider.id;
  const keyReady = sameProvider ? d.provider.source != null : false;

  // The models this provider's key can use, once there is one.
  useEffect(() => {
    if (!keyReady || !option.lists_models) return;
    call(sdk.listAiModels({ path: { workspace: ws } }))
      .then(setChoices)
      .catch(() => setChoices(null));
  }, [keyReady, option.lists_models, ws]);

  const pick = (c: ModelChoices) => {
    setChoices(c);
    if (c.suggested && (!fast || !c.models.includes(fast))) setFast(c.suggested.fast);
    if (c.suggested && (!reasoning || !c.models.includes(reasoning))) setReasoning(c.suggested.reasoning);
  };
  const saveKey = async () => {
    setBusy("key");
    setError(null);
    try {
      const c = await call(sdk.setAiProvider({ path: { workspace: ws }, body: { provider, key: key.trim() || null, api_base: base.trim() || null } }));
      setKey("");
      if (!sameProvider) {
        setFast("");
        setReasoning("");
      }
      pick(c);
      await refresh();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };
  const saveModels = async () => {
    setBusy("models");
    setError(null);
    try {
      await refresh(await call(sdk.setAiModels({ path: { workspace: ws }, body: { fast, reasoning } })));
      toast.success("Models saved");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };
  const useDefaults = async () => {
    setChoices(null);
    const next = await call(sdk.resetAiSetting({ path: { workspace: ws, what: "provider" } }));
    setProvider((next.provider.id as ProviderId | null) ?? "anthropic");
    setFast(next.fast.model ?? "");
    setReasoning(next.reasoning.model ?? "");
    await refresh(next);
  };
  const teamOwns = d.provider.source === "team" || d.fast.source === "team" || d.reasoning.source === "team";
  const canPickModels = keyReady || (sameProvider && option.has_default_key);
  const changed = fast !== (d.fast.model ?? "") || reasoning !== (d.reasoning.model ?? "");

  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Models</h3>
        <Source source={d.provider.source} hint={d.provider.hint} />
      </div>
      <p className="text-muted-foreground -mt-1 text-sm leading-relaxed">
        Pick two models from one provider: a fast one for quick jobs, and a reviewer that takes a second look at flagged calls, names each kind of mistake and drafts fixes.
      </p>
      <Segmented
        label="Provider"
        value={provider}
        options={d.providers.map((p) => [p.id, <>{PROVIDER_LOGO[p.id]}{p.label}</>] as [ProviderId, ReactNode])}
        onChange={(p) => {
          setProvider(p);
          setChoices(null);
          setError(null);
        }}
      />
      <div className="grid gap-2">
        <Label htmlFor="provider-key" className="text-sm">
          {option.label === "Other" ? "API key" : `${option.label} API key`}
          {sameProvider && d.provider.source === "team" && <span className="text-muted-foreground font-normal"> (saved {d.provider.hint})</span>}
        </Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id="provider-key"
            type="password"
            autoComplete="off"
            className="min-w-0 flex-1 basis-60"
            placeholder={option.has_default_key ? "Optional: leave empty to use the default key" : "Paste your key"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null || (!key.trim() && !option.has_default_key && provider !== "other")}
            onClick={() => void saveKey()}
          >
            {busy === "key" ? "Checking…" : key.trim() ? "Save key" : "Use this provider"}
          </Button>
        </div>
        {option.key_url && provider !== "other" && (
          <a className="text-muted-foreground w-fit text-sm underline underline-offset-2" href={option.key_url} target="_blank" rel="noreferrer">
            Where to get {/^[AEIOU]/.test(option.label) ? "an" : "a"} {option.label} key
          </a>
        )}
        {provider === "other" && (
          <>
            <Label htmlFor="provider-base" className="mt-1 text-sm">
              Base URL <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            <Input id="provider-base" placeholder="https://… for an OpenAI-compatible endpoint" value={base} onChange={(e) => setBase(e.target.value)} />
            <p className="text-muted-foreground text-sm">
              Any model{" "}
              <a className="underline underline-offset-2" href={option.key_url ?? undefined} target="_blank" rel="noreferrer">
                LiteLLM supports
              </a>
              , written with its prefix, like <code>groq/llama-3.3-70b-versatile</code>.
            </p>
          </>
        )}
      </div>

      {(canPickModels || provider === "other") && (
        <div className="grid gap-3 sm:grid-cols-2">
          <ModelField id="model-fast" label="Fast model" value={fast} onChange={setFast} choices={option.lists_models ? choices : null} source={d.fast.source} />
          <ModelField id="model-reasoning" label="Reviewer model" value={reasoning} onChange={setReasoning} choices={option.lists_models ? choices : null} source={d.reasoning.source} />
        </div>
      )}
      {(canPickModels || provider === "other") && fast.trim() && reasoning.trim() && <CostEstimateBox ws={ws} fast={fast.trim()} reasoning={reasoning.trim()} />}
      {error && (
        <Banner tone="err" className="mb-0">
          {error}
        </Banner>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {(canPickModels || provider === "other") && (
          <Button type="button" disabled={busy !== null || !fast.trim() || !reasoning.trim() || !changed} onClick={() => void saveModels()}>
            {busy === "models" ? "Trying each model…" : "Save models"}
          </Button>
        )}
        {teamOwns && (
          <Button type="button" variant="ghost" className="text-muted-foreground" onClick={() => void useDefaults()}>
            Use the defaults
          </Button>
        )}
      </div>
    </section>
  );
}

function ModelField({
  id,
  label,
  value,
  onChange,
  choices,
  source,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  choices: ModelChoices | null;
  source: "team" | "default" | null | undefined;
}) {
  const options = choices?.models.length ? (value && !choices.models.includes(value) ? [value, ...choices.models] : choices.models) : null;
  return (
    <div className="grid min-w-0 gap-2">
      <Label htmlFor={id} className="text-sm">
        {label} {source === "default" && <span className="text-muted-foreground font-normal">(default)</span>}
      </Label>
      {options ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full min-w-0 font-mono text-xs overflow-hidden *:data-[slot=select-value]:block! *:data-[slot=select-value]:min-w-0 *:data-[slot=select-value]:truncate">
            <SelectValue placeholder="Pick a model" />
          </SelectTrigger>
          <SelectContent>
            {options.map((m) => (
              <SelectItem key={m} value={m} className="font-mono text-xs">
                {m}
                {(m === choices?.suggested?.fast && id === "model-fast") || (m === choices?.suggested?.reasoning && id === "model-reasoning") ? " (recommended)" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input id={id} className="font-mono text-xs" placeholder="provider/model" value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

const ROLE_NAME = { decision: "Decisions", fast: "Fast model", reasoning: "Reviewer" } as const;

function money(n: number) {
  if (n === 0) return "$0";
  if (n < 0.001) return `$${n.toPrecision(2)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

const perMillion = (p: number) => (p < 0.1 ? `$${Number(p.toPrecision(2))}` : `$${p.toFixed(2)}`);

/** What checking calls would cost on the team's own keys with the models picked, with what the estimate leaves out. */
function CostEstimateBox({ ws, fast, reasoning }: { ws: string; fast: string; reasoning: string }) {
  const [calls, setCalls] = useState("25,000");
  const n = Number(calls.replace(/[^0-9]/g, "")) || 0;
  const query = useDeferredValue({ fast, reasoning, calls: n });
  const res = useQuery({
    queryKey: ["ws", ws, "ai", "estimate", query],
    queryFn: () => call(sdk.estimateAiCost({ path: { workspace: ws }, query })),
    placeholderData: keepPreviousData,
  });
  const e = res.data;
  return (
    <section className="grid gap-3 rounded-md border bg-muted/40 px-4 py-3" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">Estimated model cost</h4>
        <div className="flex items-center gap-2">
          <Label htmlFor="estimate-calls" className="text-muted-foreground text-sm font-normal">
            Calls a month
          </Label>
          <Input
            id="estimate-calls"
            inputMode="numeric"
            className="h-7 w-28 text-right text-sm tabular-nums"
            value={calls}
            onChange={(ev) => setCalls(ev.target.value)}
            onBlur={() => setCalls(n.toLocaleString("en-US"))}
          />
        </div>
      </div>
      {!e ? (
        <div className="bg-muted h-16 animate-pulse rounded" />
      ) : (
        <>
          <div className="grid gap-0.5 tabular-nums">
            <p className="text-lg font-semibold">
              {money(e.per_call.low)} to {money(e.per_call.high)} <span className="text-muted-foreground text-sm font-normal">a call</span>
            </p>
            <p className="text-sm">
              {money(e.per_month.low)} to {money(e.per_month.high)} a month at {e.calls_per_month.toLocaleString("en-US")} calls
            </p>
          </div>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-3 text-sm tabular-nums">
            {e.roles.map((r) => (
              <div key={r.role} className="col-span-3 grid grid-cols-subgrid items-baseline border-t py-1 first:border-t-0">
                <dt>{ROLE_NAME[r.role]}</dt>
                <dd className="text-muted-foreground truncate font-mono" title={r.model ?? undefined}>
                  {r.model}
                </dd>
                <dd className="text-right whitespace-nowrap">
                  {r.input_price != null && r.output_price != null ? (
                    <>
                      {perMillion(r.input_price)} in, {perMillion(r.output_price)} out <span className="text-muted-foreground max-sm:hidden">per million tokens</span>
                    </>
                  ) : (
                    <span className="text-warn">No price on file</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <ul className="text-muted-foreground grid list-disc gap-1 pl-4 text-sm leading-relaxed">
            <li>
              {e.basis === "yours"
                ? `Based on your last ${e.basis_calls?.toLocaleString("en-US")} checked calls, over 30 days.`
                : "Based on a typical call of a minute or two, with the reviewer taking a second look at half of them. Once sopops has checked 30 of your calls, this uses yours."}
            </li>
            <li>Cost moves with call length and with how many calls get flagged for the reviewer, so it's a range.</li>
            <li>
              List prices, before prompt caching or batch discounts.{" "}
              <strong className="text-foreground font-semibold">
                Prices come from LiteLLM's price list and can lag behind a provider's changes, so check your provider's pricing page.
              </strong>
            </li>
            <li>Covers only the models sopops uses to check calls. Your voice platform and your agent's own models aren't included.</li>
            {e.unpriced.length > 0 && <li className="text-warn">No price on file for {e.unpriced.join(" and ")}, so the totals leave it out.</li>}
          </ul>
        </>
      )}
    </section>
  );
}
