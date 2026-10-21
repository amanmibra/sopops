// A team's AI keys and models, in setup and in Settings under Checks: the TypeSafe key for Jev, a provider and its key,
// and the two models. Each is the team's own or the deployment's default; keys go in and never come back out.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { type AiSettingsOut, type ModelChoices, type ProviderIn, call, q, sdk } from "@/lib/api";
import { useWs } from "@/lib/workspace";
import { Banner, Segmented, Tag } from "./bits";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
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
  return (
    <div className="grid gap-5">
      <TypeSafeSection d={d} ws={ws} refresh={refresh} />
      <ProviderSection d={d} ws={ws} refresh={refresh} />
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
        <h3 className="text-[14px] font-semibold">TypeSafe key</h3>
        <Source source={d.typesafe.source} hint={d.typesafe.hint} />
      </div>
      <p className="text-muted-foreground text-[13px] leading-relaxed">
        Jev, from TypeSafe, answers the yes or no questions behind every check. It's required.{" "}
        <a className="font-medium underline underline-offset-2" href={d.typesafe_key_url} target="_blank" rel="noreferrer">
          Get a key
        </a>
      </p>
      <div className="flex flex-wrap gap-2">
        <Input
          id="typesafe-key"
          type="password"
          autoComplete="off"
          className="min-w-0 flex-1"
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
  const current = (d.provider.id as ProviderId | null) ?? "anthropic";
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
        <h3 className="text-[14px] font-semibold">Models</h3>
        <Source source={d.provider.source} hint={d.provider.hint} />
      </div>
      <p className="text-muted-foreground -mt-1 text-[13px] leading-relaxed">
        Two models from one provider do the rest: a fast one for quick jobs, and a reviewer for flagged checks, pattern names and suggested edits.
      </p>
      <Segmented
        label="Provider"
        value={provider}
        options={d.providers.map((p) => [p.id, p.label] as [ProviderId, string])}
        onChange={(p) => {
          setProvider(p);
          setChoices(null);
          setError(null);
        }}
      />
      <div className="grid gap-2">
        <Label htmlFor="provider-key" className="text-[13px]">
          {option.label === "Other" ? "API key" : `${option.label} API key`}
          {sameProvider && d.provider.source === "team" && <span className="text-muted-foreground font-normal"> (saved {d.provider.hint})</span>}
        </Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id="provider-key"
            type="password"
            autoComplete="off"
            className="min-w-0 flex-1"
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
          <a className="text-muted-foreground w-fit text-[12px] underline underline-offset-2" href={option.key_url} target="_blank" rel="noreferrer">
            Where to get a {option.label} key
          </a>
        )}
        {provider === "other" && (
          <>
            <Label htmlFor="provider-base" className="mt-1 text-[13px]">
              Base URL <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            <Input id="provider-base" placeholder="https://… for an OpenAI-compatible endpoint" value={base} onChange={(e) => setBase(e.target.value)} />
            <p className="text-muted-foreground text-[12px]">
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
    <div className="grid min-w-0 gap-1.5">
      <Label htmlFor={id} className="text-[13px]">
        {label} {source === "default" && <span className="text-muted-foreground font-normal">(default)</span>}
      </Label>
      {options ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-full min-w-0 font-mono text-xs [&>span]:truncate">
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
