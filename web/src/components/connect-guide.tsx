// What each platform needs to get instructions from sopops and send sessions back, and which of it takes code in the agent.
// Shown in Settings and once an import is applied. A cell marked `next` describes a path that isn't built yet, with
// what works until then.

import type { ReactNode } from "react";

import { usePlatforms } from "@/lib/platforms";
import { cn } from "@/lib/utils";
import { PlatformLabel } from "./brand-icon";
import { HrefLink } from "./href-link";

type Need = "none" | "sdk" | "enterprise";

const NEED: Record<Need, { label: string; className: string }> = {
  none: { label: "No code", className: "bg-brand-soft text-primary border-transparent" },
  sdk: { label: "Code in your agent", className: "bg-muted text-foreground" },
  enterprise: { label: "No code on ElevenLabs Enterprise", className: "bg-brand-soft text-primary border-transparent" },
};

type Cell = { need: Need; how: ReactNode; next?: ReactNode };

const ROWS: { platform: string; prompt: Cell; after: Cell; live: Cell }[] = [
  {
    platform: "livekit",
    prompt: { need: "sdk", how: <>Add <code>attach()</code> where your agent starts a call, and it loads its instructions from sopops.</> },
    after: {
      need: "none",
      how: <>Add your LiveKit project key and a webhook in LiveKit Cloud. sopops joins each call and records it.</>,
    },
    live: {
      need: "none",
      how: <>sopops hears each turn as it happens. Tool calls still need <code>attach()</code>.</>,
    },
  },
  {
    platform: "elevenlabs",
    prompt: {
      need: "none",
      how: <>Add an API key, and sopops updates the agent when you publish. For phone calls, you can also add a webhook so every call gets the newest instructions.</>,
    },
    after: {
      need: "none",
      how: <>Add a post-call webhook in ElevenLabs, and sopops gets each call when it ends.</>,
    },
    live: {
      need: "enterprise",
      how: (
        <>
          On an ElevenLabs Enterprise plan, sopops listens to each phone call that the call-start webhook serves, as it happens. On other ElevenLabs
          plans, the TypeScript SDK streams calls your app starts, and phone calls arrive when they end.
        </>
      ),
    },
  },
  {
    platform: "vapi",
    prompt: { need: "none", how: <>Add an API key, and sopops updates the assistant when you publish. You can also point a phone number's Server URL at sopops.</> },
    after: {
      need: "none",
      how: <>Vapi sends each call to that same Server URL when it ends.</>,
    },
    live: {
      need: "none",
      how: <>Turn on live updates in the assistant's settings on Vapi.</>,
    },
  },
];

function NeedTag({ need }: { need: Need }) {
  return <span className={cn("inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium", NEED[need].className)}>{NEED[need].label}</span>;
}

function CellView({ cell }: { cell: Cell }) {
  return (
    <td className="border-b px-2.5 py-2.5 align-top">
      <span className="flex flex-wrap items-center gap-1.5">
        <NeedTag need={cell.need} />
        {cell.next && <span className="text-muted-foreground rounded-full border border-dashed px-2 py-0.5 text-[11px]">Coming next</span>}
      </span>
      <div className="text-muted-foreground mt-1 max-w-[32ch] text-[13px] leading-relaxed">{cell.how}</div>
      {cell.next && <div className="text-muted-foreground mt-1 max-w-[32ch] text-xs italic">{cell.next}</div>}
    </td>
  );
}

/** Per platform the team uses: what it takes to get instructions from sopops, and to send sessions back after a call and in real time. */
export function ConnectGuide({ className }: { className?: string }) {
  const plats = usePlatforms();
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full min-w-[720px] border-collapse text-left">
        <thead>
          <tr className="text-muted-foreground text-xs">
            <th className="border-b px-2.5 py-1.5 font-medium">Platform</th>
            <th className="border-b px-2.5 py-1.5 font-medium">Instructions</th>
            <th className="border-b px-2.5 py-1.5 font-medium">Sessions, after a call</th>
            <th className="border-b px-2.5 py-1.5 font-medium">Real-time, during a call</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.filter((r) => plats.has(r.platform)).map((r) => (
            <tr key={r.platform}>
              <td className="border-b px-2.5 py-2.5 align-top text-[13px] font-semibold">
                <PlatformLabel platform={r.platform} />
              </td>
              <CellView cell={r.prompt} />
              <CellView cell={r.after} />
              <CellView cell={r.live} />
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground mt-2 text-xs">
        Code samples are in the README, under <HrefLink href="https://github.com/amanmibra/sopops#connect-your-agent">Connect your agent</HrefLink>.
      </p>
    </div>
  );
}
