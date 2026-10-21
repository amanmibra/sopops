// Platform, GitHub and Google logos, inline SVG so nothing loads from another host. The marks come from each brand kit. Black
// parts are drawn in currentColor so they follow the text colour in dark mode; LiveKit's blue squares keep their
// brand colour. ElevenLabs ships only a wordmark, so its mark is the wordmark's two bars.

import type { ReactNode } from "react";

import { platformName } from "@/lib/kinds";
import { cn } from "@/lib/utils";

type IconProps = { size?: number; className?: string };

const svgProps = (size: number, viewBox: string, className?: string) => ({
  xmlns: "http://www.w3.org/2000/svg",
  width: size,
  height: size,
  viewBox,
  fill: "none",
  "aria-hidden": true,
  focusable: false,
  className: cn("inline-block shrink-0", className),
});

const LiveKitIcon = ({ size = 16, className }: IconProps) => (
  <svg {...svgProps(size, "0 0 230 230", className)}>
    <path d="M137.884 92.4675H92.1167V138.235H137.884V92.4675Z" fill="#002CF2" />
    <path d="M183.651 46.718H137.884V92.4852H183.651V46.718Z" fill="#002CF2" />
    <path d="M183.651 138.234H137.884V184.002H183.651V138.234Z" fill="#002CF2" />
    <path d="M229.4 0.950928H183.633V46.7181H229.4V0.950928Z" fill="#002CF2" />
    <path d="M229.4 183.984H183.633V229.751H229.4V183.984Z" fill="#002CF2" />
    <path
      d="M46.3673 183.984V138.235V92.4674V46.7181V0.950928H0.600098V46.7181V92.4674V138.235V183.984V229.751H46.3673H92.1165H137.884V183.984H92.1165H46.3673Z"
      fill="currentColor"
    />
  </svg>
);

const ElevenLabsIcon = ({ size = 16, className }: IconProps) => (
  <svg {...svgProps(size, "-16.75 0 88.5 88.5", className)}>
    <path d="M0 0H18.413V88.5124H0V0Z" fill="currentColor" />
    <path d="M36.5788 0H54.9917V88.5124H36.5788V0Z" fill="currentColor" />
  </svg>
);

const VapiIcon = ({ size = 16, className }: IconProps) => (
  <svg {...svgProps(size, "2 -5.5 39 39", className)}>
    <path
      d="M17.991 13.3c1.999 1.025 4.273.502 5.72-1.215a104 104 0 0 0 2.692-3.358 4.62 4.62 0 0 1 3.65-1.81 4.52 4.52 0 0 1 4.194 2.833l6.5 16.157c.085.212-.156.41-.341.277-1.947-1.392-7.955-5.6-12.52-7.842-1.958-.96-4.165-.499-5.553 1.182-1.221 1.48-2.56 3.298-3.536 4.661a4.76 4.76 0 0 1-3.862 2.006 4.65 4.65 0 0 1-4.313-2.918l-8.54-21.3c-.09-.225.188-.426.368-.263 2.332 2.118 9.653 8.572 15.541 11.59"
      fill="currentColor"
    />
  </svg>
);

/** GitHub's Invertocat mark. */
export const GitHubIcon = ({ size = 16, className }: IconProps) => (
  <svg {...svgProps(size, "0 0 16 16", className)}>
    <path
      fillRule="evenodd"
      d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
      fill="currentColor"
    />
  </svg>
);

/** Google's "G", in its four colours (from Google's sign-in branding), on any background. */
export const GoogleIcon = ({ size = 16, className }: IconProps) => (
  <svg {...svgProps(size, "0 0 48 48", className)}>
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
  </svg>
);

const PLATFORM_ICONS: Record<string, (p: IconProps) => ReactNode> = {
  livekit: (p) => <LiveKitIcon {...p} />,
  elevenlabs: (p) => <ElevenLabsIcon {...p} />,
  vapi: (p) => <VapiIcon {...p} />,
};

/** The platform's logo, or nothing for a platform without one. */
export const PlatformIcon = ({ platform, ...p }: IconProps & { platform: string }) => PLATFORM_ICONS[platform]?.(p) ?? null;

/** The platform's logo and name, inline with text. */
export const PlatformLabel = ({ platform, size = 14, className }: IconProps & { platform: string }) => (
  <span className={cn("inline-flex items-center gap-2 align-middle", className)}>
    <PlatformIcon platform={platform} size={size} />
    {platformName(platform)}
  </span>
);
