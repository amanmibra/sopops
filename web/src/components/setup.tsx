// Setting up, one question per screen: the frame every step shares ("Step 3 of 8", a progress bar, Back and one main
// button), the platform cards, a join link to copy, and the steps that happen inside a team (people, platforms,
// agents, publishing and connecting calls). The steps before the team exists (name, email and password, team name)
// are on /welcome; joining a team is on /join.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "@tanstack/react-router";
import { ArrowLeft, Check, CircleHelp, Copy, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { type JoinLinkCreated, call, sdk } from "@/lib/api";
import { platformName, plural, when } from "@/lib/kinds";
import { ALL_PLATFORMS, type PlatformPick, listNames, platformsOf, usePlatforms } from "@/lib/platforms";
import { FLOWS, type Flow, STEP_TITLE, type StepKey, nextStep, prevStep, setupHref, setupProgress, stepNumber } from "@/lib/setup";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { settingsHref, useWs, wsHref } from "@/lib/workspace";
import { AIModelsForm, useAISettings } from "./ai-models";
import { Banner } from "./bits";
import { PlatformIcon } from "./brand-icon";
import { ConnectGuide } from "./connect-guide";
import { useGo } from "./href-link";
import { Logo } from "./logo";
import { BlocksExample } from "./onboarding/blocks-diagram";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// --- the frame ------------------------------------------------------------------------------------------

export type Primary = { label: string; disabled?: boolean; run: () => void | Promise<void> };

/** "Step 3 of 8" and a bar with one segment per step. */
export function StepProgress({ flow, step, total: count }: { flow: Flow; step: StepKey; total?: number }) {
  const n = stepNumber(flow, step);
  const steps = FLOWS[flow].slice(0, count ?? FLOWS[flow].length);
  const total = steps.length;
  return (
    <div className="mb-5">
      <div className="text-muted-foreground mb-2 text-[13px] font-medium">
        Step {n} of {total}
      </div>
      <div className="flex gap-1" role="progressbar" aria-label="Setup" aria-valuemin={1} aria-valuemax={total} aria-valuenow={n}>
        {steps.map((s, i) => (
          <span key={s} className={cn("h-1.5 flex-1 rounded-full", i < n ? "bg-primary" : "bg-muted")} />
        ))}
      </div>
    </div>
  );
}

/**
 * One step: the question, what it's for, its answer, Back, and one main button. `aside` sits left of the main button
 * (a "Skip for now"). `exit` is a quiet link in the corner, to leave setup and come back to it later.
 */
export function StepShell({
  flow,
  step,
  sub,
  children,
  back,
  primary,
  aside,
  exit,
  error,
  wide,
  total,
}: {
  flow: Flow;
  step: StepKey;
  total?: number;
  sub?: ReactNode;
  children?: ReactNode;
  back?: () => void;
  primary: Primary;
  aside?: ReactNode;
  exit?: ReactNode;
  error?: string | null;
  wide?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (busy || primary.disabled) return;
    setBusy(true);
    try {
      await primary.run();
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="flex min-h-screen items-start justify-center px-4 pt-[8vh] pb-10">
      <div className={cn("bg-card w-full rounded-xl border px-5 py-6 shadow-sm sm:px-7 sm:py-7", wide ? "max-w-[800px]" : "max-w-[520px]")}>
        <div className="mb-6 flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <Logo /> sopops
          <span className="flex-1" />
          {exit}
        </div>
        <StepProgress flow={flow} step={step} total={total} />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <h1 className="mb-2 text-[22px] leading-tight font-semibold tracking-tight">{STEP_TITLE[step]}</h1>
          {sub && <p className="text-muted-foreground mb-5 text-[14px] leading-relaxed">{sub}</p>}
          {children}
          {error && (
            <Banner tone="err" className="mt-4 mb-0">
              {error}
            </Banner>
          )}
          <div className="mt-6 flex flex-wrap items-center gap-2">
            {back && (
              <Button type="button" variant="ghost" className="-ml-2" onClick={back}>
                <ArrowLeft /> Back
              </Button>
            )}
            <span className="flex-1" />
            {aside}
            <Button type="submit" className="ml-auto h-10 min-w-28 text-[14px]" disabled={busy || primary.disabled}>
              {primary.label}
            </Button>
          </div>
        </form>
      </div>
    </main>
  );
}

// --- platforms -------------------------------------------------------------------------------------------

const PICKS: { value: PlatformPick; label: string; text: string }[] = [
  { value: "livekit", label: "LiveKit", text: "LiveKit agents" },
  { value: "elevenlabs", label: "ElevenLabs", text: "ElevenLabs agents" },
  { value: "vapi", label: "Vapi", text: "Vapi assistants" },
  { value: "other", label: "Something else", text: "Or not sure yet" },
];

/** A card per platform, with its logo. Pick any number. */
export function PlatformPicker({ value, onChange, compact }: { value: PlatformPick[]; onChange: (v: PlatformPick[]) => void; compact?: boolean }) {
  const toggle = (p: PlatformPick) => onChange(value.includes(p) ? value.filter((x) => x !== p) : PICKS.map((x) => x.value).filter((x) => x === p || value.includes(x)));
  return (
    <div role="group" aria-label="Platforms" className={cn("grid gap-2.5", compact ? "sm:grid-cols-4" : "sm:grid-cols-2")}>
      {PICKS.map((p) => {
        const on = value.includes(p.value);
        return (
          <button
            key={p.value}
            type="button"
            role="checkbox"
            aria-checked={on}
            onClick={() => toggle(p.value)}
            className={cn(
              "bg-card flex cursor-pointer items-center gap-3 rounded-xl border text-left hover:bg-muted",
              compact ? "px-3 py-2" : "px-4 py-3.5",
              on && "border-primary bg-brand-soft hover:bg-brand-soft",
            )}
          >
            <span className="flex size-8 shrink-0 items-center justify-center">
              {p.value === "other" ? <CircleHelp className="text-muted-foreground size-5" /> : <PlatformIcon platform={p.value} size={compact ? 18 : 22} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold">{p.label}</span>
              {!compact && <span className="text-muted-foreground block text-[13px]">{p.text}</span>}
            </span>
            <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border", on && "border-primary bg-primary text-primary-foreground")}>
              {on && <Check className="size-3.5" />}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Save the team's platforms, and refresh everything that shows them. */
export function useSavePlatforms() {
  const qc = useQueryClient();
  return async (platforms: PlatformPick[]) => {
    await call(sdk.setOrgPlatforms({ body: { platforms } }));
    await qc.invalidateQueries({ queryKey: ["auth"] });
  };
}

/** Settings → Agents and platforms: add or take away platforms. Nothing about one is lost when it's taken away. */
export function PlatformsYouUse() {
  const auth = useAuth().data;
  const plats = platformsOf(auth?.platforms);
  const save = useSavePlatforms();
  const role = auth?.org?.role;
  const canChange = !auth?.required || role === "owner" || role === "admin" || plats.selected === null;
  const [value, setValue] = useState<PlatformPick[]>(plats.selected ?? ALL_PLATFORMS);
  const [busy, setBusy] = useState(false);
  const changed = [...value].sort().join() !== [...(plats.selected ?? ALL_PLATFORMS)].sort().join();
  const submit = async () => {
    setBusy(true);
    try {
      await save(value);
      toast.success("Saved. sopops shows only these platforms.");
    } catch (e) {
      toast.error(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="bg-card mb-4 rounded-xl border px-4 py-3.5">
      <h2 className="mb-1 text-base font-semibold">Platforms you use</h2>
      <p className="text-muted-foreground mb-3 max-w-prose text-[13px] leading-relaxed">
        sopops shows only these. Taking one away hides it and keeps everything already set up for it.
      </p>
      {canChange ? (
        <>
          <PlatformPicker value={value} onChange={setValue} compact />
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" disabled={busy || !changed || value.length === 0} onClick={() => void submit()}>
              Save
            </Button>
            {value.length === 0 && <span className="text-muted-foreground text-[13px]">Pick at least one.</span>}
          </div>
        </>
      ) : (
        <p className="text-[13px]">
          {listNames(plats.shown.map(platformName), "and")}. <span className="text-muted-foreground">Owners and admins change these.</span>
        </p>
      )}
    </section>
  );
}

// --- a link to copy ----------------------------------------------------------------------------------------

export function CopyLink({ link, label }: { link: string; label: string }) {
  const copy = () => navigator.clipboard.writeText(link).then(() => toast.success("Copied."), (e) => toast.error(message(e)));
  return (
    <div className="bg-card flex items-center gap-2 rounded-md border px-2.5 py-1.5">
      <code className="flex-1 font-mono text-[13px] break-all">{link}</code>
      <Button type="button" size="sm" variant="outline" aria-label={label} onClick={() => void copy()}>
        <Copy /> Copy
      </Button>
    </div>
  );
}

const ROLE_LABEL = { member: "Member", admin: "Admin" } as const;
type JoinRole = keyof typeof ROLE_LABEL;

export function RoleSelect({ value, onChange, id }: { value: JoinRole; onChange: (r: JoinRole) => void; id: string }) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as JoinRole)}>
      <SelectTrigger id={id} size="sm" className="w-32">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(ROLE_LABEL) as JoinRole[]).map((r) => (
          <SelectItem key={r} value={r}>
            {ROLE_LABEL[r]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// --- the steps inside a team ---------------------------------------------------------------------------------

/** Where each step's main button goes, and the bar that leads back here. */
function useSteps(flow: Flow, step: StepKey) {
  const { ws } = useWs();
  const go = useGo();
  const qc = useQueryClient();
  const to = (s: StepKey) => {
    setupProgress.set(ws, flow, s);
    if (s === "team") return void go(`/welcome?step=team`);
    void go(setupHref(ws, s, flow));
  };
  const finish = async () => {
    setupProgress.clear(ws);
    await qc.invalidateQueries({ queryKey: ["auth"] });
    void go(wsHref(ws));
  };
  const next = nextStep(flow, step);
  const prev = prevStep(flow, step);
  return {
    ws,
    go,
    next: () => (next ? to(next) : finish()),
    /** Back, except to a step that made the account: that can't be undone here. */
    back: prev && prev !== "account" && prev !== "name" ? () => to(prev) : undefined,
    /** Leave setup for another page, and keep the step to come back to. */
    leave: (href: string) => {
      setupProgress.set(ws, flow, step);
      void go(href);
    },
    exit:
      flow === "join" ? undefined : (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-muted-foreground font-normal"
          onClick={() => {
            setupProgress.set(ws, flow, step);
            void go(wsHref(ws));
          }}
        >
          Finish later
        </Button>
      ),
    skip: (label = "Skip for now") => (
      <Button type="button" variant="ghost" className="text-muted-foreground" onClick={() => (next ? to(next) : void finish())}>
        {label}
      </Button>
    ),
  };
}

export function TeamSetup({ flow, step }: { flow: Flow; step: StepKey }) {
  switch (step) {
    case "people":
      return <PeopleStep flow={flow} />;
    case "models":
      return <ModelsStep flow={flow} />;
    case "platforms":
      return <PlatformsStep flow={flow} />;
    case "blocks":
      return <BlocksStep flow={flow} />;
    case "agents":
      return <AgentsStep flow={flow} />;
    case "publish":
      return <PublishStep flow={flow} />;
    default:
      return <ConnectStep flow={flow} />;
  }
}

function PeopleStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "people");
  const auth = useAuth().data;
  const team = auth?.org?.name ?? "your team";
  const [role, setRole] = useState<JoinRole>("member");
  const [made, setMade] = useState<JoinLinkCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const make = async (r: JoinRole) => {
    setError(null);
    try {
      setMade(await call(sdk.createJoinLink({ body: { role: r, days: 7 } })));
    } catch (e) {
      setError(message(e));
    }
  };
  return (
    <StepShell
      flow={flow}
      step="people"
      sub={<>Share a link. Anyone who opens it can make an account and join {team}.</>}
      back={s.back}
      exit={s.exit}
      error={error}
      aside={!made && s.skip()}
      primary={made ? { label: "Continue", run: s.next } : { label: "Get a join link", run: () => make(role) }}
    >
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Label htmlFor="join-role" className="text-[13px]">
          People join as
        </Label>
        <RoleSelect
          id="join-role"
          value={role}
          onChange={(r) => {
            setRole(r);
            if (made) void make(r); // a new link with the new role; the old one stops working
          }}
        />
      </div>
      {made && (
        <div className="grid gap-2">
          <CopyLink link={made.link} label="Copy the join link" />
          <p className="text-muted-foreground text-[13px] leading-relaxed">
            It works until {new Date(made.expires_at).toLocaleDateString(undefined, { month: "long", day: "numeric" })}. Turn it off or make a new one in Settings,
            under Members.
          </p>
        </div>
      )}
    </StepShell>
  );
}

function ModelsStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "models");
  const ai = useAISettings().data;
  const defaults = ai?.ready && ai.typesafe.source === "default" && ai.fast.source === "default" && ai.reasoning.source === "default";
  return (
    <StepShell
      flow={flow}
      step="models"
      wide
      sub={
        <>
          sopops checks every call with these. Keys are stored encrypted and never shown again. You can change them later in Settings, under Checks.
        </>
      }
      back={s.back}
      exit={s.exit}
      aside={!ai?.ready && s.skip()}
      primary={{ label: "Continue", disabled: !ai?.ready, run: s.next }}
    >
      {defaults && (
        <Banner tone="ok">
          <Check className="mr-1.5 inline size-4" />
          This sopops comes with keys and models already. Continue, or use your own below.
        </Banner>
      )}
      <AIModelsForm />
      {!ai?.ready && <p className="text-muted-foreground mt-4 text-[13px]">Until then, calls are saved but not checked.</p>}
    </StepShell>
  );
}

function PlatformsStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "platforms");
  const plats = usePlatforms();
  const save = useSavePlatforms();
  const [value, setValue] = useState<PlatformPick[]>(plats.selected ?? []);
  const [error, setError] = useState<string | null>(null);
  return (
    <StepShell
      flow={flow}
      step="platforms"
      sub="Pick every one your agents run on. sopops shows only these, and you can change them in Settings."
      back={s.back}
      exit={plats.selected ? s.exit : undefined} // home asks again until someone picks
      error={error}
      primary={{
        label: "Continue",
        disabled: value.length === 0,
        run: async () => {
          setError(null);
          try {
            await save(value);
            s.next();
          } catch (e) {
            setError(message(e));
          }
        },
      }}
    >
      <PlatformPicker value={value} onChange={setValue} />
    </StepShell>
  );
}

function BlocksStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "blocks");
  return (
    <StepShell
      flow={flow}
      step="blocks"
      wide
      sub={
        <>
          Most teams run several agents that are alike in some ways and different in others: one for each restaurant, customer or task. sopops splits their instructions
          into blocks. Write a block once and every agent that uses it gets it. Change it once and all of those agents change together, so nobody edits each agent by
          hand. Whatever is unique to one agent stays with that agent.
          <span className="mt-2 block text-[13px]">Instructions are what your platform may call the prompt or system prompt.</span>
        </>
      }
      back={s.back}
      exit={s.exit}
      primary={{ label: "Continue", run: s.next }}
    >
      <BlocksExample />
    </StepShell>
  );
}

function AgentsStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "agents");
  const { cfg } = useWs();
  const n = cfg.agents.length;
  return (
    <StepShell
      flow={flow}
      step="agents"
      sub="Import each agent's full instructions as they run today. sopops splits them into shared instructions and procedures. You review everything before it's saved."
      back={s.back}
      exit={s.exit}
      aside={!n && s.skip()}
      primary={n ? { label: "Continue", run: s.next } : { label: "Import agents", run: () => s.leave(wsHref(s.ws, "import")) }}
    >
      {n > 0 && (
        <Banner tone="ok" className="mb-0">
          <Check className="mr-1.5 inline size-4" />
          {plural(n, "agent is", "agents are")} in. <button type="button" className="font-semibold underline underline-offset-2" onClick={() => s.leave(wsHref(s.ws, "import"))}>Import more</button>
        </Banner>
      )}
    </StepShell>
  );
}

function PublishStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "publish");
  const { releases, draft, cfg } = useWs();
  const done = releases.length > 0;
  const nothing = !done && draft.items.length === 0;
  return (
    <StepShell
      flow={flow}
      step="publish"
      sub="Agents get their instructions from a release. Look over the drafts, then publish them."
      back={s.back}
      exit={s.exit}
      aside={!done && !nothing && s.skip()}
      primary={done || nothing ? { label: "Continue", run: s.next } : { label: "Review and publish", run: () => s.leave(wsHref(s.ws, "changes")) }}
    >
      {done && (
        <Banner tone="ok" className="mb-0">
          <Check className="mr-1.5 inline size-4" />
          {plural(releases.length, "release is", "releases are")} published, the last {when(releases.reduce((a, b) => (a.number > b.number ? a : b)).created_at)}.
        </Banner>
      )}
      {nothing && (
        <Banner className="mb-0">
          {cfg.agents.length ? "Nothing new to publish." : "There's nothing to publish until your agents are in. You can come back to this from your team's home page."}
        </Banner>
      )}
    </StepShell>
  );
}

function ConnectStep({ flow }: { flow: Flow }) {
  const s = useSteps(flow, "connect");
  const plats = usePlatforms();
  const done = useCallsConnected(s.ws);
  return (
    <StepShell
      flow={flow}
      step="connect"
      wide
      sub={
        <>
          Each agent gets its instructions from sopops and sends its calls back to be checked. Here's what {listNames(plats.shown.map(platformName), "and")}{" "}
          {plats.shown.length === 1 ? "needs" : "need"}.
        </>
      }
      back={s.back}
      exit={s.exit}
      aside={!done && s.skip("Finish for now")}
      primary={done ? { label: "Finish", run: s.next } : { label: "Add keys and webhooks", run: () => s.leave(`${settingsHref(s.ws, "platforms")}?from=qa`) }}
    >
      {done && (
        <Banner tone="ok">
          <Check className="mr-1.5 inline size-4" />
          Calls are connected.
        </Banner>
      )}
      <ConnectGuide />
    </StepShell>
  );
}

/** Whether calls can reach sopops: a platform connected or a webhook secret made, or a session already in. */
export function useCallsConnected(ws: string): boolean {
  const capture = useQuery({ queryKey: ["ws", ws, "runtime", "capture"], queryFn: () => call(sdk.getCapture({ path: { workspace: ws } })) });
  const creds = useQuery({ queryKey: ["ws", ws, "runtime", "platforms"], queryFn: () => call(sdk.listPlatforms({ path: { workspace: ws } })) });
  const sessions = useFirstSession(ws);
  const c = capture.data;
  return Boolean(
    c?.livekit.connected || c?.elevenlabs.post_call_secret.set || c?.elevenlabs.last_post_call_at || creds.data?.some((p) => p.webhook_secret.set) || sessions,
  );
}

export function useFirstSession(ws: string): boolean | undefined {
  const q = useQuery({ queryKey: ["ws", ws, "sessions", "first"], queryFn: () => call(sdk.listSessions({ path: { workspace: ws }, query: { limit: 1 } })) });
  return q.data ? q.data.items.length > 0 : undefined;
}

/** While setup is under way in this browser: which step is next, and a way back to it (or out of it). */
export function SetupBar({ ws }: { ws: string }) {
  const go = useGo();
  const { pathname } = useLocation();
  const [, closed] = useState(0);
  const at = setupProgress.get(ws);
  if (!at || pathname === wsHref(ws) || pathname === `${wsHref(ws)}/`) return null;
  return (
    <div className="bg-brand-soft mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-3.5 py-2 text-[13px]">
      <span className="font-medium">
        Setup, step {stepNumber(at.flow, at.step)} of {FLOWS[at.flow].length}: {STEP_TITLE[at.step]}
      </span>
      <span className="flex-1" />
      <Button size="sm" variant="outline" className="h-7" onClick={() => void go(setupHref(ws, at.step, at.flow))}>
        Back to setup
      </Button>
      <Button
        size="icon-sm"
        variant="ghost"
        aria-label="Stop showing setup"
        onClick={() => {
          setupProgress.clear(ws);
          closed((n) => n + 1);
        }}
      >
        <X />
      </Button>
    </div>
  );
}
