"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { MeResponse, Permission } from "@fieldops/shared";
import { ApiError, api } from "./api";

type SessionState = {
  session: MeResponse | null;
  status: "loading" | "signed-in" | "signed-out";
  can: (permission: Permission) => boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<MeResponse | null>(null);
  const [status, setStatus] = useState<SessionState["status"]>("loading");
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    // A cookie should survive a reload, so the app asks who it is talking to
    // before rendering anything that depends on the answer. A 401 here is the
    // ordinary "not signed in" reply, not an error worth surfacing.
    api
      .me()
      .then((value) => {
        if (!cancelled) {
          setSession(value);
          setStatus("signed-in");
        }
      })
      .catch(() => {
        if (!cancelled) setStatus("signed-out");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const value = await api.login(email, password);
    setSession(value);
    setStatus("signed-in");
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setSession(null);
    setStatus("signed-out");
    router.push("/sign-in");
  }, [router]);

  const value = useMemo<SessionState>(
    () => ({
      session,
      status,
      can: (permission) => session?.permissions.includes(permission) ?? false,
      signIn,
      signOut,
    }),
    [session, status, signIn, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}

/**
 * Sends a signed-out visitor to the sign-in page.
 *
 * A client-side guard, and only a convenience: the API authorises every request
 * on its own. Bypassing this reveals an empty shell that can fetch nothing.
 */
export function useRequireSession(): SessionState {
  const state = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (state.status === "signed-out") {
      const next = pathname && pathname !== "/" ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/sign-in${next}`);
    }
  }, [state.status, router, pathname]);

  return state;
}

/** Session expiring mid-session is normal; treat it as a sign-out, not a bug. */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.code === "unauthenticated";
}
