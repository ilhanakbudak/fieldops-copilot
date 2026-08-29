"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Citation, DoneEvent, RetrievedSource, TokenUsage, ToolRun } from "@fieldops/shared";
import { api } from "@/lib/api";
import { askStream } from "@/lib/chat";
import { useConversations } from "@/lib/conversations";
import { useSession } from "@/lib/session";
import { Markdown } from "@/components/Markdown";
import { ResultRows } from "@/components/ResultRows";
import { Button, Input } from "@/components/ui";
import { ChevronIcon, CopyIcon, PencilIcon } from "@/components/icons";
import styles from "./chat.module.css";

type Turn = {
  id: string;
  /** The stored id of the question, needed to edit it. Absent while streaming. */
  messageId: string | null;
  question: string;
  answer: string;
  citations: Citation[];
  sources: RetrievedSource[];
  tools: ToolRun[];
  running: string[];
  usage: TokenUsage | null;
  error: string | null;
  streaming: boolean;
};

const SUGGESTIONS = [
  "What does error code E-04 mean?",
  "What is today's date?",
  "Pull up John Smith in Portland",
  "Why has the water been warm since the radon system was installed?",
];

/** Tool names are for the model. These are for the person reading. */
const TOOL_LABELS: Record<string, string> = {
  search_knowledge_base: "Searching the knowledge base",
  find_customer: "Looking up the customer",
  get_customer_detail: "Reading the customer record",
  mcp_time_get_current_time: "Checking the time",
  mcp_time_convert_time: "Converting a time",
};

function label(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/^mcp_[a-z0-9]+_/, "").replace(/_/g, " ");
}

/**
 * One line in the "what happened" trail.
 *
 * A step that came back with data expands to show it; one that did not stays a
 * line. The same chip-then-body shape as a citation, because it answers the
 * same question — *where did that come from* — and a second interaction idiom
 * for the same question is one the reader has to learn twice.
 */
function ToolStep({ tool }: { tool: ToolRun }) {
  const [open, setOpen] = useState(false);
  const rows = tool.data?.rows ?? [];

  if (rows.length === 0) {
    return (
      <span className={styles.step}>
        <span className={`${styles.stepDot} ${tool.ok ? "" : styles.stepDotFailed}`} aria-hidden />
        {label(tool.name)} — {tool.summary}
      </span>
    );
  }

  return (
    <div className={styles.stepGroup}>
      <button
        type="button"
        className={`${styles.step} ${styles.stepButton} ${open ? styles.stepOpen : ""}`}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className={`${styles.stepDot} ${tool.ok ? "" : styles.stepDotFailed}`} aria-hidden />
        {label(tool.name)} — {tool.summary}
        <ChevronIcon className={`${styles.stepChevron} ${open ? styles.stepChevronOpen : ""}`} />
      </button>
      {open && (
        <div className={styles.stepBody}>
          <ResultRows rows={rows} />
        </div>
      )}
    </div>
  );
}

function blankTurn(question: string): Turn {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    messageId: null,
    question,
    answer: "",
    citations: [],
    sources: [],
    tools: [],
    running: [],
    usage: null,
    error: null,
    streaming: true,
  };
}

export default function ChatPage() {
  const { session } = useSession();
  const history = useConversations();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  // The conversation this page created and is streaming into. Without it, the
  // effect below fires the moment a new conversation gets an id and replaces
  // the live turn — tool trail, retrieval detail and all — with the stored
  // version, which has none of them.
  const owned = useRef<string | null>(null);

  const activeId = history.activeId;

  // Selecting a conversation in the sidebar loads it here. Rebuilt from stored
  // messages, so the retrieval detail — which describes how an answer was
  // produced rather than what it says — is not restored.
  useEffect(() => {
    let cancelled = false;

    if (!activeId) {
      owned.current = null;
      setTurns([]);
      return;
    }

    if (activeId === owned.current) return;

    void api.conversation(activeId).then((detail) => {
      if (cancelled) return;
      const restored: Turn[] = [];
      for (let index = 0; index < detail.messages.length; index += 1) {
        const message = detail.messages[index]!;
        if (message.role !== "user") continue;
        const reply = detail.messages[index + 1];
        restored.push({
          ...blankTurn(message.content),
          id: message.id,
          messageId: message.id,
          answer: reply?.role === "assistant" ? reply.content : "",
          citations: reply?.citations ?? [],
          streaming: false,
        });
      }
      setTurns(restored);
    });

    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => () => abort.current?.abort(), []);

  const update = useCallback((id: string, patch: Partial<Turn>) => {
    setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));
  }, []);

  const send = useCallback(
    async (text: string, editMessageId?: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;

      const turn = blankTurn(trimmed);
      setTurns((current) =>
        editMessageId
          ? // Editing replaces this question and everything after it. The server
            // does the same to the stored thread, so the two stay in step.
            [
              ...current.slice(
                0,
                current.findIndex((item) => item.messageId === editMessageId),
              ),
              turn,
            ]
          : [...current, turn],
      );
      setQuestion("");
      setBusy(true);

      const controller = new AbortController();
      abort.current = controller;

      let answer = "";
      const tools: ToolRun[] = [];
      const sources: RetrievedSource[] = [];

      try {
        for await (const event of askStream(
          trimmed,
          activeId,
          controller.signal,
          editMessageId ?? null,
        )) {
          if (event.type === "start") {
            owned.current = event.data.conversationId;
            if (!activeId) history.select(event.data.conversationId);
          } else if (event.type === "tool") {
            update(turn.id, { running: [...turn.running, label(event.data.name)] });
          } else if (event.type === "tool_done") {
            tools.push(event.data);
            update(turn.id, { tools: [...tools], running: [] });
          } else if (event.type === "sources") {
            sources.push(...event.data.sources);
            update(turn.id, { sources: [...sources] });
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
            void history.refresh();
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
        update(turn.id, { streaming: false, running: [] });
        setBusy(false);
        abort.current = null;
        composer.current?.focus();
      }
    },
    [busy, activeId, history, update],
  );

  return (
    <>
      <div className={styles.thread}>
        {turns.length === 0 ? (
          <div className={styles.welcome}>
            <div>
              <p className={styles.welcomeTitle}>
                Hello{session ? `, ${session.user.fullName.split(" ")[0]}` : ""}
              </p>
              <p className={styles.welcomeBody}>
                Ask about equipment, procedures or a customer. I will use the documents your
                role can read, look an account up in the CRM, or answer directly when neither
                is needed.
              </p>
            </div>
            <div className={styles.suggestions}>
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
          </div>
        ) : (
          turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              busy={busy}
              onEdit={(text) => void send(text, turn.messageId ?? undefined)}
            />
          ))
        )}
        <div ref={bottom} />
      </div>

      <div className={styles.composer}>
        <form
          className={styles.composerInner}
          onSubmit={(event) => {
            event.preventDefault();
            void send(question);
          }}
        >
          <textarea
            ref={composer}
            className={styles.composerField}
            rows={1}
            value={question}
            placeholder="Ask anything"
            aria-label="Ask a question"
            onChange={(event) => {
              setQuestion(event.target.value);
              // Grow to fit, up to the max-height in CSS.
              event.target.style.height = "auto";
              event.target.style.height = `${event.target.scrollHeight}px`;
            }}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter breaks the line. The convention every
              // messaging interface shares, and the one people's hands expect.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(question);
              }
            }}
          />
          {busy ? (
            <button
              type="button"
              className={styles.send}
              onClick={() => abort.current?.abort()}
              aria-label="Stop generating"
            >
              <span
                style={{ width: 10, height: 10, background: "#fff", borderRadius: 2 }}
                aria-hidden
              />
            </button>
          ) : (
            <button
              type="submit"
              className={styles.send}
              disabled={!question.trim()}
              aria-label="Send"
            >
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 19V5m0 0-6 6m6-6 6 6" />
              </svg>
            </button>
          )}
        </form>
        <p className={styles.composerHint}>
          Answers come only from documents your role can read. Every claim is cited.
        </p>
      </div>
    </>
  );
}

function TurnView({
  turn,
  busy,
  onEdit,
}: {
  turn: Turn;
  busy: boolean;
  onEdit: (text: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(turn.question);

  const known = new Set(turn.citations.map((citation) => citation.marker));
  const cited = turn.citations.find((citation) => citation.marker === open);

  if (editing) {
    return (
      <article className={styles.turn}>
        <div className={styles.editBox}>
          <Input value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus />
          <div className={styles.editActions}>
            <Button size="small" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              size="small"
              variant="primary"
              disabled={!draft.trim()}
              onClick={() => {
                setEditing(false);
                onEdit(draft);
              }}
            >
              Ask again
            </Button>
          </div>
        </div>
      </article>
    );
  }

  return (
    <article className={styles.turn}>
      <p className={styles.question}>{turn.question}</p>

      {turn.messageId && !busy && (
        <div className={styles.actions}>
          <button
            className={styles.action}
            onClick={() => {
              setDraft(turn.question);
              setEditing(true);
            }}
            aria-label="Edit this question"
            title="Edit and ask again"
          >
            <PencilIcon />
          </button>
          <button
            className={styles.action}
            onClick={() => void navigator.clipboard?.writeText(turn.answer)}
            aria-label="Copy the answer"
            title="Copy answer"
          >
            <CopyIcon />
          </button>
        </div>
      )}

      <div className={styles.answerRow}>
        <span className={styles.avatar} aria-hidden>
          FO
        </span>

        <div className={styles.answer}>
          {(turn.tools.length > 0 || turn.running.length > 0) && (
            <div className={styles.steps}>
              {turn.tools.map((tool, index) => (
                <ToolStep key={`${tool.name}-${index}`} tool={tool} />
              ))}
              {turn.running.map((name, index) => (
                <span className={styles.step} key={`running-${index}`}>
                  <span className={`${styles.stepDot} ${styles.stepDotRunning}`} aria-hidden />
                  {name}…
                </span>
              ))}
            </div>
          )}

          {turn.error ? (
            <p className={styles.error} role="alert">
              {turn.error}
            </p>
          ) : (
            <>
              <Markdown
                content={turn.answer}
                known={known}
                active={open}
                onMarker={(marker) => setOpen(open === marker ? null : marker)}
              />
              {turn.streaming && !turn.answer && (
                <span className={styles.caret} aria-hidden />
              )}
            </>
          )}

          {turn.citations.length > 0 && (
            <div className={styles.citations}>
              {turn.citations.map((citation) => (
                <button
                  key={citation.marker}
                  type="button"
                  className={`${styles.citation} ${
                    open === citation.marker ? styles.citationOpen : ""
                  }`}
                  onClick={() => setOpen(open === citation.marker ? null : citation.marker)}
                  aria-expanded={open === citation.marker}
                >
                  <span className={styles.citationMarker}>{citation.marker}</span>
                  <span className={styles.citationLabel}>
                    {citation.section ?? citation.documentTitle}
                    {citation.page ? ` · p${citation.page}` : ""}
                  </span>
                </button>
              ))}
            </div>
          )}

          {cited && (
            <blockquote className={styles.citationBody}>
              <cite className={styles.citationSource}>
                {cited.documentTitle}
                {cited.section ? ` · ${cited.section}` : ""}
                {cited.page ? ` · page ${cited.page}` : ""}
              </cite>
              {cited.snippet}
            </blockquote>
          )}

          {turn.sources.length > 0 && !turn.streaming && (
            <details className={styles.detail}>
              <summary className={styles.detailSummary}>
                {turn.sources.length} passages retrieved
                {turn.usage
                  ? ` · ${turn.usage.inputTokens + turn.usage.outputTokens} tokens`
                  : ""}
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
        </div>
      </div>
    </article>
  );
}
