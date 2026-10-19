// Per-browser preferences in localStorage.
export const prefs = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(`sopops.${key}`);
    } catch {
      return null;
    }
  },
  remove(key: string) {
    try {
      localStorage.removeItem(`sopops.${key}`);
    } catch {
      /* private mode */
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
