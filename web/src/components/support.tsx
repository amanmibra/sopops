// Support sessions: one of sopops' own support staff working in a customer's workspace to help them, with the same
// hands as a member of their team. Three pieces live here. The band every page wears while a session is running
// (SupportBanner), the page staff start one from (StartSupportSession, /support), and what the customer sees of it in
// Settings (SupportPanel).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LifeBuoy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { type AuthState, ApiError, type SupportState, call, sdk } from "@/lib/api";
import { when } from "@/lib/kinds";
import { DEFAULT_MINUTES, LENGTHS, START_HREF, clock, span, supportQ, useCountdown } from "@/lib/support";
import { Banner, ErrorBanner, Note, Panel, Tag } from "./bits";
import { useGo } from "./href-link";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { DropdownMenuItem } from "./ui/dropdown-menu";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fail = (e: unknown) => toast.error(message(e));
/** The feature isn't set up on this sopops: every support route answers 404, as if it weren't there. */
const missing = (e: unknown) => e instanceof ApiError && e.status === 404;
/** Nobody signs in here, so there's nobody for support to help. */
const noSignIn = (e: unknown) => e instanceof ApiError && e.status === 409;

/** While a support session is running, this band sits above everything, on every page: whose workspace is on screen,
 *  how long is left, and the way out. It can't be dismissed. */
export function SupportBanner({ auth }: { auth: AuthState }) {
  const state = useQuery(supportQ.state(Boolean(auth.user)));
  const running = Boolean(state.data?.running);
  const was = useRef(false);
  // The session has died of its own accord: load the app again as yourself, so none of the customer's data is left on
  // screen without the band over it.
  useEffect(() => {
    if (was.current && !running) window.location.assign("/");
    was.current = running;
  }, [running]);
  return state.data?.running ? <Band state={state.data} /> : null;
}

function Band({ state }: { state: SupportState }) {
  const qc = useQueryClient();
  const left = useCountdown(state.until);
  const [ending, setEnding] = useState(false);
  const band = useRef<HTMLDivElement>(null);

  // The page sits below the band: the top bar and the rails stick under it, by its height (--band-h).
  useEffect(() => {
    const el = band.current;
    if (!el) return;
    const measure = () => document.documentElement.style.setProperty("--band-h", `${el.offsetHeight}px`);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => {
      watch.disconnect();
      document.documentElement.style.setProperty("--band-h", "0px");
    };
  }, []);

  // The clock has run out: ask the server, so the band goes on its own rather than waiting for the next poll.
  useEffect(() => {
    if (left === 0) void qc.invalidateQueries({ queryKey: ["support", "state"] });
  }, [left, qc]);

  const end = async () => {
    setEnding(true);
    try {
      await call(sdk.endSupportSession());
    } catch (e) {
      setEnding(false);
      fail(e);
      return;
    }
    window.location.assign("/"); // a full load: nothing of the customer's stays in a cache
  };

  const ends = when(state.until);
  return (
    <div ref={band} role="region" aria-label="Support session" className="bg-err text-background sticky top-0 z-30 border-b border-black/20">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 md:px-6">
        <span className="min-w-0 flex-1 text-sm" title={`Ends at ${ends}`}>
          Support session · <b className="font-semibold">{state.org_name || state.org_id}</b> · ends in <span className="tabular-nums">{clock(left)}</span>
        </span>
        <button
          type="button"
          disabled={ending}
          onClick={() => void end()}
          className="t-label border-background hover:bg-background hover:text-err cursor-pointer border px-3 py-1 disabled:opacity-60"
        >
          End
        </button>
      </div>
    </div>
  );
}

/** In the user menu, for staff only: the way to the page a support session starts from. */
export function SupportMenuLink() {
  const go = useGo();
  const state = useQuery(supportQ.state());
  if (!state.data?.staff || state.data.running) return null;
  return (
    <DropdownMenuItem onSelect={() => void go(START_HREF)}>
      <LifeBuoy className="size-4" /> Support session
    </DropdownMenuItem>
  );
}

/** Start a support session: which team, and for how long. Staff only — anyone else is turned away, the way the
 *  server turns them away. */
export function StartSupportSession() {
  const state = useQuery(supportQ.state());
  const staff = Boolean(state.data?.staff) && !state.data?.running;
  const teams = useQuery(supportQ.teams(staff));
  const [target, setTarget] = useState("");
  const [minutes, setMinutes] = useState(String(DEFAULT_MINUTES));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.isPending) return <Note>Loading…</Note>;
  if (missing(state.error)) return <Banner tone="info">Support sessions aren't set up on this sopops.</Banner>;
  if (noSignIn(state.error)) return <Banner tone="info">Nobody signs in on this sopops.</Banner>;
  if (state.isError) return <ErrorBanner error={state.error} lead="sopops didn't answer:" />;
  if (state.data.running) return <Banner tone="warn">You're in a session on {state.data.org_name || state.data.org_id}. End it first.</Banner>;
  if (!state.data.staff) return <Banner tone="info">This page is for sopops support staff.</Banner>;

  const rows = teams.data ?? [];
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(sdk.startSupportSession({ body: { org: target, minutes: Number(minutes) } }));
    } catch (e) {
      setError(message(e));
      setBusy(false);
      return;
    }
    // A full load, as the customer's member: nothing of your own account stays cached, and / lands in their workspace.
    window.location.assign("/");
  };

  return (
    <form
      className="grid max-w-[420px] gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        void start();
      }}
    >
      <div className="grid gap-1">
        <Label htmlFor="support-team">Team</Label>
        <Select value={target} onValueChange={setTarget} disabled={teams.isPending || rows.length === 0}>
          <SelectTrigger id="support-team" className="w-full">
            <SelectValue placeholder={teams.isPending ? "…" : "Team"} />
          </SelectTrigger>
          <SelectContent>
            {rows.map((t) => (
              <SelectItem key={t.id} value={t.id} disabled={!t.support_access}>
                {t.name}
                <span className="text-muted-foreground">
                  {[t.workspaces.join(", "), t.support_access ? null : "support access off"].filter(Boolean).join(" · ")}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {teams.isError && <ErrorBanner error={teams.error} lead="Teams didn't load:" />}
      </div>
      <div className="grid gap-1">
        <Label htmlFor="support-minutes">For</Label>
        <Select value={minutes} onValueChange={setMinutes}>
          <SelectTrigger id="support-minutes" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LENGTHS.map((l) => (
              <SelectItem key={l.minutes} value={String(l.minutes)}>
                {l.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {error && <Banner tone="err">{error}</Banner>}
      <div>
        <Button type="submit" disabled={busy || !target}>
          Start
        </Button>
        <Note className="mt-2 block">They see every session in their settings.</Note>
      </div>
    </form>
  );
}

/** Settings → Support access: the switch a team turns support off with, and every session run against them. Owners
 *  and admins change the switch; everyone else sees where it stands. */
export function SupportPanel({ auth }: { auth: AuthState }) {
  const qc = useQueryClient();
  const role = auth.org?.role;
  const manager = role === "owner" || role === "admin";
  const support = useQuery(supportQ.org());
  const [busy, setBusy] = useState(false);

  if (missing(support.error)) return <Banner tone="info">Support sessions aren't set up on this sopops.</Banner>;
  if (noSignIn(support.error)) return <Banner tone="info">Nobody signs in on this sopops.</Banner>;

  const set = async (allowed: boolean) => {
    setBusy(true);
    try {
      await call(sdk.setOrgSupport({ body: { support_access: allowed } }));
      await qc.invalidateQueries({ queryKey: ["org", "support"] });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const data = support.data;
  const sessions = data?.sessions ?? [];
  return (
    <>
      {support.isError && <ErrorBanner error={support.error} lead="Support access didn't load:" />}
      <Panel>
        <div className="flex items-center gap-2">
          <Checkbox
            id="support-access"
            checked={Boolean(data?.support_access)}
            disabled={!manager || busy || !data}
            onCheckedChange={(v) => void set(v === true)}
          />
          <Label htmlFor="support-access" className="text-sm">
            Allow support sessions
          </Label>
        </div>
      </Panel>
      <Panel>
        {sessions.length === 0 ? (
          <p className="text-muted-foreground text-sm">None yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b">
                  <th className="py-2 pr-3">Who</th>
                  <th className="py-2 pr-3 whitespace-nowrap">Started</th>
                  <th className="py-2 pr-3 whitespace-nowrap">Length</th>
                  <th className="py-2 text-right">Requests</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id} className="border-b align-top">
                    <td className="py-2 pr-3">
                      <span className="block break-all">{s.staff_email}</span>
                      {s.running && (
                        <Tag tone="changed" className="mt-1">
                          Open now
                        </Tag>
                      )}
                    </td>
                    <td className="text-muted-foreground py-2 pr-3 whitespace-nowrap">{when(s.started_at)}</td>
                    <td className="text-muted-foreground py-2 pr-3 whitespace-nowrap">{span(s.started_at, s.until)}</td>
                    <td className="py-2 text-right tabular-nums">{s.requests}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
