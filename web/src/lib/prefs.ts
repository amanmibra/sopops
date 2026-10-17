// Per-browser preferences in localStorage. Keys from before the rename (sopserve.*) are still read.
export const prefs = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(`sopops.${key}`) ?? localStorage.getItem(`sopserve.${key}`);
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
