// The last workspace page you had open (outside Settings and setup), per workspace and tab, so Settings can send you back.
import { wsHref } from "./workspace";

const key = (ws: string) => `sopops.nav.${ws}.last`;
const outside = (pathname: string) => !/^\/w\/[^/]+\/(settings|setup)(\/|$)/.test(pathname);

export function rememberPage(ws: string, pathname: string, searchStr: string) {
  if (!outside(pathname) || pathname.endsWith("/new")) return;
  try {
    sessionStorage.setItem(key(ws), pathname + searchStr);
  } catch {
    /* private mode */
  }
}

export function lastPageHref(ws: string): string {
  try {
    const last = sessionStorage.getItem(key(ws));
    if (last && decodeURIComponent(last).startsWith(`/w/${ws}/`)) return last;
  } catch {
    /* private mode */
  }
  return wsHref(ws);
}
