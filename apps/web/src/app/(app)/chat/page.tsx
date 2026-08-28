"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type {
  Citation,
  ConversationSummary,
  DoneEvent,
  RetrievedSource,
  TokenUsage,
} from "@fieldops/shared";
import { api } from "@/lib/api";
import { askStream } from "@/lib/chat";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import { Button, EmptyState, Input } from "@/components/ui";
import styles from "./chat.module.css";

type Turn = {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  sources: RetrievedSource[];
  usage: TokenUsage | null;
  retrievalMs: number | null;
  candidates: number | null;
  reranker: string | null;
  error: string | null;
  streaming: boolean;
};

const SUGGESTIONS = [
  "What does error code E-04 mean?",
  "Why has the water been warm since the radon system was installed?",
  "What is our warranty on a radon water system?",
  "Which treatment removes a rotten egg smell?",
];

function newTurn(question: string): Turn {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    question,
    answer: "",
    citations: [],
    sources: [],
    usage: null,
    retrievalMs: null,
    candidates: null,
    reranker: null,
    error: null,
    streaming: true,
  };
}

export default function ChatPage() {
  const { session } = useSession();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const refreshConversations = useCallback(async () => {
    setConversations(await api.conversations().catch(() => []));
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  // Follow the answer as it streams. `block: "end"` rather than scrollIntoView's
  // default, so the composer stays put instead of the page jumping.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  // Leaving the page mid-answer should stop the request, not leave it writing
  // into a component that no longer exists.
  useEffect(() => () => abort.current?.abort(), []);

  const update = useCallback((id: string, patch: Partial<Turn>) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
    );
  }, []);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    const turn = newTurn(trimmed);
    setTurns((current) => [...current, turn]);
    setQuestion("");
    setBusy(true);

    const controller = new AbortController();
    abort.current = controller;

    let answer = "";
    try {
      for await (const event of askStream(trimmed, conversationId, controller.signal)) {
        if (event.type === "sources") {
          setConversationId(event.data.conversationId);
          update(turn.id, {
            sources: event.data.sources,
            retrievalMs: event.data.retrievalMs,
            candidates: event.data.candidates,
            reranker: event.data.reranker,
          });
        } else if (event.type === "delta") {
          answer += event.data.text;
          update(turn.id, { answer });
        } else if (event.type === "done") {
          const done: DoneEvent = event.data;
          update(turn.id, {
            answer: done.text,
            citations: done.citations,
            usage: done.usage,
            streaming: false,
          });
          void refreshConversations();
        } else {
          update(turn.id, { error: event.data.message, streaming: false });
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        update(turn.id, {
          error: cause instanceof Error ? cause.message : "The answer stopped unexpectedly.",
          streaming: false,
        });
      }
    } finally {
      update(turn.id, { streaming: false });
      setBusy(false);
      abort.current = null;
    }
  }

  async function open(id: string) {
    abort.current?.abort();
    const detail = await api.conversation(id).catch(() => null);
    if (!detail) return;

    // Rebuild the thread from stored messages. The retrieval detail is not
    // persisted — it describes how an answer was produced, not what it says —
    // so a reopened conversation shows the answer and its citations only.
    const restored: Turn[] = [];
    for (let index = 0; index < detail.messages.length; index += 1) {
      const message = detail.messages[index]!;
      if (message.role !== "user") continue;
      const reply = detail.messages[index + 1];
      restored.push({
        ...newTurn(message.content),
        id: message.id,
        answer: reply?.role === "assistant" ? reply.content : "",
        citations: reply?.citations ?? [],
        streaming: false,
      });
    }

    setTurns(restored);
    setConversationId(id);
  }

  function startNew() {
    abort.current?.abort();
    setTurns([]);
    setConversationId(null);
    setQuestion("");
  }

  return (
    <>
      <PageHeader
        title="Company AI"
        subtitle="Answers drawn only from the documents your role can read, with the source and page for every claim."
      />

      <div className={styles.layout}>
        <aside className={styles.sidebar} aria-label="Conversations">
          <Button className={styles.newButton} onClick={startNew}>
            New conversation
          </Button>
          <div className={styles.conversationList}>
            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                className={`${styles.conversationItem} ${
                  conversation.id === conversationId ? styles.conversationItemActive : ""
                }`}
                onClick={() => void open(conversation.id)}
                title={conversation.title}
              >
                {conversation.title}
              </button>
            ))}
          </div>
        </aside>

        <div>
          {turns.length === 0 ? (
            <EmptyState
              title={`Ask anything in the documents you can read, ${session?.user.fullName.split(" ")[0] ?? ""}`.trim()}
              body="Every answer cites the document, section and page it came from. A claim with no citation did not come from your knowledge base."
              action={
                <div className={styles.suggestions} style={{ marginTop: 8 }}>
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className={styles.suggestion}
                      onClick={() => void send(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              }
            />
          ) : (
            <div className={styles.thread}>
              {turns.map((turn) => (
                <TurnView key={turn.id} turn={turn} />
              ))}
            </div>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className={styles.composer}>
        <form
          className={styles.composerInner}
          onSubmit={(event) => {
            event.preventDefault();
            void send(question);
          }}
        >
          <Input
            className={styles.composerField}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask a question"
            aria-label="Ask a question"
            disabled={busy}
          />
          {busy ? (
            <Button type="button" onClick={() => abort.current?.abort()}>
              Stop
            </Button>
          ) : (
            <Button type="submit" variant="primary" disabled={!question.trim()}>
              Ask
            </Button>
          )}
        </form>
      </div>
    </>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <article className={styles.turn}>
      <p className={styles.question}>{turn.question}</p>

      {turn.error ? (
        <p className={styles.answer} role="alert" style={{ color: "var(--danger-700)" }}>
          {turn.error}
        </p>
      ) : (
        <p className={styles.answer}>
          <AnswerText
            text={turn.answer}
            citations={turn.citations}
            active={open}
            onMarker={(marker) => setOpen(open === marker ? null : marker)}
          />
          {turn.streaming && <span className={styles.caret} aria-hidden />}
        </p>
      )}

      {turn.citations.length > 0 && (
        <div className={styles.citations}>
          {turn.citations.map((citation) => (
            <button
              key={citation.marker}
              type="button"
              className={`${styles.citation} ${open === citation.marker ? styles.citationOpen : ""}`}
              onClick={() => setOpen(open === citation.marker ? null : citation.marker)}
              aria-expanded={open === citation.marker}
            >
              <span className={styles.citationMarker}>{citation.marker}</span>
              {/* Section as well as title: three chips reading "Service Manual
                  · p3" tell a reader nothing about which is which. */}
              {citation.section ?? citation.documentTitle}
              {citation.page ? ` · p${citation.page}` : ""}
            </button>
          ))}
        </div>
      )}

      {open &&
        turn.citations
          .filter((citation) => citation.marker === open)
          .map((citation) => (
            <blockquote className={styles.citationBody} key={citation.marker}>
              <cite className={styles.citationSource}>
                {citation.documentTitle}
                {citation.section ? ` · ${citation.section}` : ""}
                {citation.page ? ` · page ${citation.page}` : ""}
              </cite>
              {citation.snippet}
            </blockquote>
          ))}

      {turn.sources.length > 0 && !turn.streaming && (
        <details className={styles.detail}>
          <summary className={styles.detailSummary}>
            {turn.sources.length} passages from {turn.candidates} candidates ·{" "}
            {turn.retrievalMs}ms · {turn.reranker}
            {turn.usage ? ` · ${turn.usage.inputTokens + turn.usage.outputTokens} tokens` : ""}
            {turn.usage && turn.usage.estimatedCostUsd > 0
              ? ` · $${turn.usage.estimatedCostUsd.toFixed(4)}`
              : ""}
          </summary>
          <div className={styles.detailBody}>
            {turn.sources.map((source) => (
              <div className={styles.sourceRow} key={source.marker}>
                <span className={styles.sourceRank}>{source.marker}</span>
                <span className={styles.sourceTitle}>
                  {source.documentTitle}
                  {source.section ? ` · ${source.section}` : ""}
                </span>
                <span className={styles.sourceMeta}>
                  {/* Which leg found it, and where. The first thing worth
                      knowing when a result looks wrong. */}
                  {source.ranks.vector ? `v${source.ranks.vector}` : "v—"}{" "}
                  {source.ranks.keyword ? `k${source.ranks.keyword}` : "k—"}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

/**
 * Renders `[S1]` markers as interactive chips.
 *
 * A marker that survived server-side resolution always has a citation behind
 * it, so an unmatched one here means the text was restored from a stored
 * message whose citations were trimmed — render it as plain text rather than as
 * a chip that does nothing.
 */
function AnswerText({
  text,
  citations,
  active,
  onMarker,
}: {
  text: string;
  citations: Citation[];
  active: string | null;
  onMarker: (marker: string) => void;
}) {
  const known = new Set(citations.map((citation) => citation.marker));
  const parts = text.split(/(\[S\d+\])/g);

  return (
    <>
      {parts.map((part, index) => {
        const match = /^\[(S\d+)\]$/.exec(part);
        if (!match || !known.has(match[1]!)) return <Fragment key={index}>{part}</Fragment>;

        const marker = match[1]!;
        return (
          <button
            key={index}
            type="button"
            className={`${styles.marker} ${active === marker ? styles.markerActive : ""}`}
            onClick={() => onMarker(marker)}
            title={`Show source ${marker}`}
          >
            {marker}
          </button>
        );
      })}
    </>
  );
}
