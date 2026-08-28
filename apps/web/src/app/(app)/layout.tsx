"use client";

import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { ConversationProvider } from "@/lib/conversations";
import { useRequireSession } from "@/lib/session";
import { Skeleton } from "@/components/ui";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { status } = useRequireSession();

  // A skeleton rather than a spinner: the shell is about to occupy this space,
  // so reserving it keeps the page from jumping when the session resolves.
  if (status !== "signed-in") {
    return (
      <div style={{ display: "grid", gap: 16, maxWidth: 1120, margin: "0 auto", padding: 40 }}>
        <Skeleton height={32} width="240px" />
        <Skeleton height={20} width="420px" />
        <Skeleton height={280} />
      </div>
    );
  }

  return (
    <ConversationProvider>
      <AppShell>{children}</AppShell>
    </ConversationProvider>
  );
}
