// What each platform needs to get prompts from sopops and send sessions back, and which of it takes code in the agent.
// Shown in Settings and once an import is applied.

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { PlatformLabel } from "./brand-icon";
import { HrefLink } from "./href-link";

type Need = "none" | "sdk" | "api";

const NEED: Record<Need, { label: string; className: string }> = {
  none: { label: "No code", className: "bg-brand-soft text-primary border-transparent" },
  sdk: { label: "SDK in your agent", className: "bg-muted text-foreground" },
  api: { label: "Code on your server", className: "bg-muted text-foreground" },
};

type Cell = { need: Need; how: ReactNode };

const ROWS: { platform: string; prompts: Cell; sessions: Cell }[] = [
  {
    platform: "livekit",
    prompts: {
      need: "sdk",
      how: (
        <>
          <code>attach()</code> in the agent's entrypoint (Python), or <code>fetchInstructions</code> (TypeScript). Without the SDK, one HTTP GET with a
          runtime token.
        </>
      ),
    },
    sessions: { need: "sdk", how: <>The same <code>attach()</code> sends each call when it ends. Without it, post the transcript to the API.</> },
  },
  {
    platform: "elevenlabs",
    prompts: { need: "none", how: <>Push on publish with an API key, or the call-start webhook for phone calls. Both are under Platforms in Settings.</> },
    sessions: {
      need: "sdk",
      how: (
        <>
          <code>elevenLabsSession</code> from the TypeScript SDK for calls your app starts. Phone calls: post the transcript to the API.
        </>
      ),
    },
  },
  {
    platform: "vapi",
    prompts: { need: "none", how: <>Push on publish with an API key, or the phone number's Server URL. Both are under Platforms in Settings.</> },
    sessions: { need: "api", how: <>Post the transcript to the API when a call ends.</> },
  },
];

function NeedTag({ need }: { need: Need }) {
  return <span className={cn("inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap", NEED[need].className)}>{NEED[need].label}</span>;
}

function CellView({ cell }: { cell: Cell }) {
  return (
    <td className="border-b px-2.5 py-2.5 align-top">
      <NeedTag need={cell.need} />
      <div className="text-muted-foreground mt-1 max-w-[36ch] text-[13px] leading-relaxed">{cell.how}</div>
    </td>
  );
}

/** Per platform: what it takes to get prompts from sopops and to send sessions back for checks. */
export function ConnectGuide({ className }: { className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full min-w-[560px] border-collapse text-left">
        <thead>
          <tr className="text-muted-foreground text-xs">
            <th className="border-b px-2.5 py-1.5 font-medium">Platform</th>
            <th className="border-b px-2.5 py-1.5 font-medium">Prompts from sopops</th>
            <th className="border-b px-2.5 py-1.5 font-medium">Sessions back, for checks</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => (
            <tr key={r.platform}>
              <td className="border-b px-2.5 py-2.5 align-top text-[13px] font-semibold">
                <PlatformLabel platform={r.platform} />
              </td>
              <CellView cell={r.prompts} />
              <CellView cell={r.sessions} />
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground mt-2 text-xs">
        Setup and code samples: <HrefLink href="https://github.com/amanmibra/sopops#connect-your-agent">Connect your agent</HrefLink>. The SDKs need a token
        with the <b>runtime</b> and <b>ingest</b> scopes, made under Tokens.
      </p>
    </div>
  );
}
