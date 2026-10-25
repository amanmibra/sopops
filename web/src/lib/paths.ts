// Where this UI and its API live. Both default to the root of the origin that serves the page, which is how sopops
// serves itself. A build for a sub-path sets them (web/vite.config.ts): `SOPOPS_WEB_BASE=/app/ SOPOPS_WEB_API_BASE=/api`.
//
// Paths inside the app (`/w/demo`, `/sign-in`, a `next` to come back to) never carry the base: the router adds it to
// its own links, and `appHref` adds it where a path leaves the router (a full page load, an `<a href>`).

/** The UI's base as the router's basepath: "/" or "/app". */
export const BASEPATH = import.meta.env.BASE_URL === "/" ? "/" : import.meta.env.BASE_URL.replace(/\/+$/, "");

/** The API's base, without a trailing slash: "" (this origin's root) or "/api". */
export const API_BASE = (import.meta.env.VITE_SOPOPS_API_BASE ?? "").replace(/\/+$/, "");

const prefix = BASEPATH === "/" ? "" : BASEPATH;
const under = (path: string, base: string) => path === base || path.startsWith(`${base}/`);

/** A path in the app as a path on the site: `/w/demo` is `/app/w/demo` under `/app/`. A path already under the base,
 * or one that isn't a path on this site, is left as it is. */
export function appHref(path: string): string {
  if (!prefix || !path.startsWith("/") || path.startsWith("//") || under(path, prefix)) return path;
  return prefix + path;
}

/** A path on the site as a path in the app: the base taken off. */
export function appPath(path: string = window.location.pathname): string {
  if (!prefix || !under(path, prefix)) return path;
  return path.slice(prefix.length) || "/";
}

/** An API path (`/v1/...`) as the browser requests it. */
export const apiUrl = (path: string) => `${API_BASE}${path}`;

/** The API's full address, for showing to someone who sets up a webhook or an SDK. */
export const apiOrigin = () => `${window.location.origin}${API_BASE}`;

/** A full page load of a path in the app. */
export const assignApp = (path: string) => window.location.assign(appHref(path));
