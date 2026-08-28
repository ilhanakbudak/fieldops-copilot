"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Button, Field, Input } from "@/components/ui";
import styles from "./signin.module.css";

/**
 * Four synthetic employees, one per role. The buttons fill the form rather than
 * signing in directly, so what a reviewer clicks is still a real credential
 * going through the real endpoint.
 */
const DEMO_ACCOUNTS = [
  { email: "admin@example.com", label: "Admin" },
  { email: "office@example.com", label: "Office" },
  { email: "sales@example.com", label: "Sales" },
  { email: "tech@example.com", label: "Technician" },
];
const DEMO_PASSWORD = "demo-password-1234";

function SignInForm() {
  const { status, signIn } = useSession();
  const router = useRouter();
  const params = useSearchParams();

  const [email, setEmail] = useState(DEMO_ACCOUNTS[0]!.email);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const next = params.get("next") ?? "/knowledge";

  useEffect(() => {
    if (status === "signed-in") router.replace(next);
  }, [status, router, next]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      router.replace(next);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Something went wrong. Please try again.",
      );
      setBusy(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.mark} aria-hidden>
            FO
          </span>
          <span className={styles.brandName}>FieldOps Copilot</span>
        </div>

        <div className={styles.panel}>
          <h1 className={styles.title}>Sign in</h1>
          <p className={styles.subtitle}>Use your company account.</p>

          <form className={styles.form} onSubmit={submit} noValidate>
            {error && (
              <p className={styles.alert} role="alert">
                {error}
              </p>
            )}

            <Field label="Email">
              {(props) => (
                <Input
                  {...props}
                  type="email"
                  autoComplete="username"
                  required
                  autoFocus
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  invalid={Boolean(error)}
                />
              )}
            </Field>

            <Field label="Password">
              {(props) => (
                <Input
                  {...props}
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  invalid={Boolean(error)}
                />
              )}
            </Field>

            <Button type="submit" variant="primary" loading={busy}>
              {busy ? "Signing in" : "Sign in"}
            </Button>
          </form>

          <div className={styles.demo}>
            <p className={styles.demoLabel}>Demo accounts</p>
            <p className={styles.demoNote}>
              Four synthetic employees. Each sees a different slice of the corpus — sign in as a
              technician, then as sales, and search for “dealer cost”.
            </p>
            <div className={styles.roles}>
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  className={`${styles.role} ${email === account.email ? styles.roleActive : ""}`}
                  onClick={() => {
                    setEmail(account.email);
                    setPassword(DEMO_PASSWORD);
                    setError(null);
                  }}
                >
                  {account.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <p className={styles.footer}>Synthetic data. No real customer records.</p>
      </div>
    </main>
  );
}

export default function SignInPage() {
  // `useSearchParams` needs a Suspense boundary, or the whole route opts out of
  // static rendering.
  return (
    <Suspense fallback={<main className={styles.page} />}>
      <SignInForm />
    </Suspense>
  );
}
