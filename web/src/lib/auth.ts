// Who's signed in. The server says whether sign-in is on (`required`); with it off there's no user and the app works as
// it always did, with the "Your name" box recording authors. Signed in, the server records the user instead.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type AuthState, call, q, sdk } from "./api";

let current: AuthState | null = null;

/** The latest sign-in state, for code outside React (the author of a change). */
export const signedIn = () => Boolean(current?.user);

export function useAuth() {
  const res = useQuery(q.auth());
  current = res.data ?? current;
  return res;
}

/** Sign out, then start over at the sign-in page (or `to`). */
export function useSignOut(to = "/sign-in") {
  const qc = useQueryClient();
  return async () => {
    await call(sdk.signOut());
    qc.clear();
    window.location.assign(to);
  };
}

/** Where each provider's button starts signing in, coming back to `next`. */
export const signInHref = (provider: string, next: string) =>
  `/v1/auth/login/${encodeURIComponent(provider)}?next=${encodeURIComponent(next)}`;

// An invite link's token waits here while its holder signs in, so it never goes in a URL the server logs.
const INVITE_KEY = "sopops.invite";
export const pendingInvite = {
  get: () => {
    try {
      return sessionStorage.getItem(INVITE_KEY);
    } catch {
      return null;
    }
  },
  set: (token: string | null) => {
    try {
      if (token) sessionStorage.setItem(INVITE_KEY, token);
      else sessionStorage.removeItem(INVITE_KEY);
    } catch {
      /* private mode */
    }
  },
};
