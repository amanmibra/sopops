// The voice platforms a team said it uses. The app shows only those; a team that never picked (or picked only
// "something else") sees them all. Data already on another platform (an agent on Vapi, a saved key) still shows.
import type { PlatformsOut } from "./api";
import { useAuth } from "./auth";

export type ShownPlatform = PlatformsOut["shown"][number];
export type PlatformPick = NonNullable<PlatformsOut["selected"]>[number];

export const ALL_PLATFORMS: ShownPlatform[] = ["livekit", "elevenlabs", "vapi"];

export type Platforms = {
  /** The platforms to show. Never empty. */
  shown: ShownPlatform[];
  /** What the team picked; null until someone picks. */
  selected: PlatformPick[] | null;
  has: (p: string) => boolean;
  /** The platform new agents start on: the team's own when it uses one, else LiveKit (or the first shown). */
  first: ShownPlatform;
};

export function platformsOf(p: PlatformsOut | null | undefined): Platforms {
  const shown = p?.shown?.length ? p.shown : ALL_PLATFORMS;
  return {
    shown,
    selected: p?.selected ?? null,
    has: (x) => (shown as string[]).includes(x),
    first: shown.includes("livekit") ? "livekit" : shown[0],
  };
}

export function usePlatforms(): Platforms {
  return platformsOf(useAuth().data?.platforms);
}

/** "LiveKit, ElevenLabs or Vapi", for copy. */
export function listNames(names: string[], last = "or"): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} ${last} ${names[names.length - 1]}`;
}
