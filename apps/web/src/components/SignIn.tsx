"use client";

import { useEffect, useState } from "react";
import type { MeResponse, Permission } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";

/**
 * The whole of milestone 1, made visible: sign in as each of the four demo
 * employees and watch the permission list change.
 *
 * The permissions rendered here are informational. Every one of them is
 * enforced in the API — this panel shows what the server decided, it does not
 * decide anything itself.
 */
const ALL_PERMISSIONS: Permission[] = [
  "documents:read",
  "documents:manage",
  "customers:read",
  "inventory:read",
  "pricing:read",
  "calls:assist",
  "users:manage",
  "audit:read",
  "cost:read",
];

const DEMO_ACCOUNTS = [
  { email: "admin@example.com", label: "Admin" },
  { email: "office@example.com", label: "Office" },
  { email: "sales@example.com", label: "Sales" },
  { email: "tech@example.com", label: "Technician" },
];
const DEMO_PASSWORD = "demo-password-1234";

export function SignIn() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [email, setEmail] = useState(DEMO_ACCOUNTS[0]!.email);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  // An existing cookie should survive a reload; a 401 here is the ordinary
  // "not signed in" answer and not worth showing as an error.
  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.code === "network") setError(cause.message);
      })
      .finally(() => setBusy(false));
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api.login(email, password));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    await api.logout().catch(() => undefined);
    setMe(null);
    setBusy(false);
  }

  if (me) {
    const held = new Set(me.permissions);
    return (
      <section className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17 }}>{me.user.fullName}</h2>
            <p style={{ margin: "2px 0 0", color: "var(--text-2)", fontSize: 14 }}>
              {me.user.email} · {me.user.role}
            </p>
          </div>
          <button className="button button--quiet" onClick={signOut} disabled={busy}>
            Sign out
          </button>
        </div>

        <p style={{ margin: "18px 0 8px", fontSize: 13, color: "var(--text-2)" }}>
          Permissions granted by this role
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {ALL_PERMISSIONS.map((permission) => (
            <span
              key={permission}
              className={held.has(permission) ? "chip chip--held" : "chip"}
              style={held.has(permission) ? undefined : { opacity: 0.4 }}
            >
              {permission}
            </span>
          ))}
        </div>

        <p style={{ margin: "18px 0 0", fontSize: 13, color: "var(--text-2)" }}>
          Documents visible to this role: {me.documentRoles.join(", ")}
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2 style={{ margin: "0 0 4px", fontSize: 17 }}>Employee sign-in</h2>
      <p style={{ margin: "0 0 18px", color: "var(--text-2)", fontSize: 14 }}>
        Four synthetic accounts, one per role. The password is the same for all of them.
      </p>

      {error && <p className="error">{error}</p>}

      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <button className="button" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 16 }}>
        {DEMO_ACCOUNTS.map((account) => (
          <button
            key={account.email}
            type="button"
            className="chip"
            style={{ cursor: "pointer", background: "transparent" }}
            onClick={() => {
              setEmail(account.email);
              setPassword(DEMO_PASSWORD);
            }}
          >
            {account.label}
          </button>
        ))}
      </div>
    </section>
  );
}
