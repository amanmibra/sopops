// Per-browser preferences in localStorage.
export const prefs = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(`sopops.${key}`);
    } catch {
      return null;
    }
  },
  set(key: string, value: string) {
    try {
      localStorage.setItem(`sopops.${key}`, value);
    } catch {
      /* private mode */
    }
  },
};
