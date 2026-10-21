// The side rail on wider screens: full (icons and labels) or collapsed to a strip of icons. This browser remembers it.
import { useSyncExternalStore } from "react";
import { prefs } from "./prefs";

let collapsed = prefs.get("rail") === "collapsed";
const listeners = new Set<() => void>();

export const setRailCollapsed = (v: boolean) => {
  collapsed = v;
  if (v) prefs.set("rail", "collapsed");
  else prefs.remove("rail");
  listeners.forEach((f) => f());
};

export const useRailCollapsed = () =>
  useSyncExternalStore(
    (f) => {
      listeners.add(f);
      return () => listeners.delete(f);
    },
    () => collapsed,
  );

/** The page grid's columns at md and up, by rail state. */
export const railCols = (c: boolean) => (c ? "md:grid-cols-[56px_minmax(0,1fr)]" : "md:grid-cols-[264px_minmax(0,1fr)]");
