"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ConversationSummary } from "@fieldops/shared";
import { api } from "./api";

/**
 * Conversation history, shared between the sidebar and the chat page.
 *
 * It lives here rather than in the chat page because the sidebar renders it,
 * and the sidebar belongs to the application shell. The alternative — a second
 * navigation column inside the chat view — puts two lists of links side by side
 * and makes the reader work out which one is the app and which one is the page.
 */
type ConversationState = {
  conversations: ConversationSummary[];
  activeId: string | null;
  loading: boolean;
  select: (id: string | null) => void;
  refresh: () => Promise<void>;
  remove: (id: string) => Promise<void>;
  clear: () => Promise<void>;
};

const ConversationContext = createContext<ConversationState | null>(null);

export function ConversationProvider({ children }: { children: ReactNode }) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setConversations(await api.conversations().catch(() => []));
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const remove = useCallback(
    async (id: string) => {
      // Removed locally first. The list is the user's own and the request
      // cannot fail in a way that makes the row come back — waiting for the
      // round trip only makes deletion feel unreliable.
      setConversations((current) => current.filter((item) => item.id !== id));
      setActiveId((current) => (current === id ? null : current));
      await api.deleteConversation(id).catch(() => undefined);
      await refresh();
    },
    [refresh],
  );

  const clear = useCallback(async () => {
    setConversations([]);
    setActiveId(null);
    await api.clearConversations().catch(() => undefined);
    await refresh();
  }, [refresh]);

  const value = useMemo<ConversationState>(
    () => ({ conversations, activeId, loading, select: setActiveId, refresh, remove, clear }),
    [conversations, activeId, loading, refresh, remove, clear],
  );

  return (
    <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>
  );
}

export function useConversations(): ConversationState {
  const value = useContext(ConversationContext);
  if (!value) throw new Error("useConversations must be used inside ConversationProvider");
  return value;
}
