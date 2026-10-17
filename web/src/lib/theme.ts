import { useEffect, useState } from "react";
import { prefs } from "./prefs";

export type Theme = "light" | "dark" | "system";

const media = () => window.matchMedia("(prefers-color-scheme: dark)");

export function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && media().matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function storedTheme(): Theme {
  const t = prefs.get("theme");
  return t === "light" || t === "dark" ? t : "system";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    const m = media();
    const on = () => applyTheme("system");
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [theme]);
  return {
    theme,
    resolved: theme === "system" ? (media().matches ? "dark" : "light") : theme,
    setTheme(t: Theme) {
      prefs.set("theme", t);
      setTheme(t);
    },
  };
}
