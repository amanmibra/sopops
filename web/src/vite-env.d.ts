/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the UI calls the API (vite.config.ts sets it from SOPOPS_WEB_API_BASE): "" for this origin's root. */
  readonly VITE_SOPOPS_API_BASE?: string;
}
