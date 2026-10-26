// Settings → Usage: how many calls the workspace sent in a month (or a range you pick), and how they fall by day.
// GET …/usage/calls defines the count; months and days are UTC, the same as the server's.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { q } from "@/lib/api";
import { useWs } from "@/lib/workspace";
import { ErrorBanner, Note, Panel } from "./bits";
import { AXIS, ChartTip, Skel } from "./insights/kit";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

const EARLIER_MONTHS = 5; // the months the picker offers before this one
const DAY = 86_400_000;

/** The 1st of the UTC month `back` months before this one, as a Date at midnight UTC. */
function monthStart(back: number, now = new Date()) {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
}

const monthName = (d: Date) => d.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
const dayName = (iso: string, year = false) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: year ? "numeric" : undefined, timeZone: "UTC" });
const isoDay = (d: Date) => d.toISOString().slice(0, 10);

type Period = { start: string; end: string; label: string };

/** "0" this month, "1" last month, …; "custom" the two dates below the picker (both included). */
function period(choice: string, from: string, to: string): Period | null {
  if (choice === "custom") {
    if (!from || !to || to < from) return null;
    return { start: `${from}T00:00:00Z`, end: new Date(Date.parse(`${to}T00:00:00Z`) + DAY).toISOString(), label: `from ${dayName(from, true)} to ${dayName(to, true)}` };
  }
  const back = Number(choice);
  const s = monthStart(back);
  return { start: s.toISOString(), end: monthStart(back - 1).toISOString(), label: `in ${monthName(s)}${back === 0 ? " so far" : ""}` };
}

export function CallUsagePanel() {
  const { ws } = useWs();
  const [choice, setChoice] = useState("0");
  const [from, setFrom] = useState(() => isoDay(monthStart(0)));
  const [to, setTo] = useState(() => isoDay(new Date()));
  const p = period(choice, from, to);
  const res = useQuery({ ...q.calls(ws, p?.start ?? "", p?.end ?? ""), enabled: !!p });
  const d = res.data;
  const today = isoDay(new Date());
  const days = (d?.daily ?? []).filter((x) => x.day <= today).map((x) => ({ x: x.day, calls: x.calls }));
  const year = days.length > 0 && days[0].x.slice(0, 4) !== days[days.length - 1].x.slice(0, 4);
  return (
    <Panel title="Calls">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="usage-period">Period</Label>
          <Select value={choice} onValueChange={setChoice}>
            <SelectTrigger id="usage-period" className="min-w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Array.from({ length: EARLIER_MONTHS + 1 }, (_, back) => (
                <SelectItem key={back} value={String(back)}>
                  {back === 0 ? "This month" : back === 1 ? "Last month" : monthName(monthStart(back))}
                </SelectItem>
              ))}
              <SelectItem value="custom">Custom range</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {choice === "custom" && (
          <>
            <div className="grid gap-1">
              <Label htmlFor="usage-from">From</Label>
              <Input id="usage-from" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="usage-to">To</Label>
              <Input id="usage-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
            </div>
          </>
        )}
      </div>
      {!p ? (
        <Note>Pick a start and an end date.</Note>
      ) : res.error ? (
        <ErrorBanner error={res.error} />
      ) : !d ? (
        <Skel className="h-40 w-full" />
      ) : (
        <div className="grid gap-4">
          <div>
            <div className="text-3xl font-semibold tabular-nums">{d.calls.toLocaleString()}</div>
            <div className="text-muted-foreground text-sm">
              {d.calls === 1 ? "call" : "calls"} {p.label}
            </div>
          </div>
          {days.length > 1 && (
            <div className="h-40" aria-label="Calls per day">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={days} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--grid)" />
                  <XAxis dataKey="x" {...AXIS} tickFormatter={(x: string) => dayName(x)} minTickGap={24} />
                  <YAxis {...AXIS} allowDecimals={false} width={44} />
                  <Tooltip
                    cursor={{ fill: "var(--muted)" }}
                    content={(t) => <ChartTip {...t} head={(x) => dayName(x, year)} />}
                  />
                  <Bar dataKey="calls" name="Calls" fill="var(--primary)" maxBarSize={24} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
      <p className="text-muted-foreground mt-4 text-xs">
        A call is a session sopops received in full: sent whole, or streamed and closed (an open one counts once it closes). Calls count by when they
        started. Months and days are UTC.
      </p>
    </Panel>
  );
}
