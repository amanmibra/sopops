/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the UI calls the API (vite.config.ts sets it from SOPOPS_WEB_API_BASE): "" for this origin's root. */
  readonly VITE_SOPOPS_API_BASE?: string;
  /** "1" for the demo build: the API answered from a recording (lib/demo.ts). */
  readonly VITE_SOPOPS_DEMO?: string;
  readonly VITE_SOPOPS_DEMO_WS?: string;
  readonly VITE_SOPOPS_DEMO_SIGN_UP?: string;
}
