"use client";

import { useEffect, useRef } from "react";
import type { CallScript, CallSuggestion, Utterance } from "@fieldops/shared";
import { Markdown } from "@/components/Markdown";
import { Badge, Button } from "@/components/ui";
import { SplitPane } from "@/components/SplitPane";
import { useAssist } from "./useAssist";
import styles from "./call.module.css";

/**
 * The screen while somebody is on the phone.
 *
 * Two columns and a divider between them, because there is no split that suits
 * both ways of using this: one person watches the transcript and glances at
 * suggestions, the other reads a suggestion aloud and glances at the
 * transcript. See `SplitPane`.
 *
 * The suggestion column shows refusals as well as answers. An assistant that
 * says nothing for a minute is indistinguishable from one that has crashed, and
 * the employee has no way to check while they are talking to a customer.
 */
export function LiveAssist({ script }: { script: CallScript | null }) {
  const assist = useAssist();
  const running = assist.state === "connecting" || assist.state === "listening";

  return (
    <section className={styles.assist} aria-label="Live call assistance">
      <header className={styles.assistBar}>
        <span className={styles.status}>
          <span className={`${styles.dot} ${running ? styles.dotLive : ""}`} aria-hidden />
          {STATUS[assist.state]}
        </span>

        {assist.capped && <Badge tone="warning">Suggestion limit reached</Badge>}

        {running ? (
          <Button size="small" onClick={assist.stop}>
            End call
          </Button>
        ) : (
          <Button size="small" variant="primary" onClick={() => assist.start(script)}>
            {assist.state === "closed" ? "Replay the call" : "Start a call"}
          </Button>
        )}
      </header>

      {assist.state === "idle" ? (
        <p className={styles.assistIdle}>
          A scripted call, replayed from the browser over the same WebSocket a
          transcription service would use. The pause detector, the actionability
          classifier, retrieval and generation all run for real — only the
          microphone is fake.
        </p>
      ) : (
        <SplitPane
          storageKey="fieldops.call.split"
          initial={45}
          min={28}
          max={70}
          startLabel="Transcript"
          endLabel="Suggestions"
          start={
            <Transcript
              utterances={assist.utterances}
              partial={assist.partial}
              listening={running}
            />
          }
          end={
            <Suggestions
              suggestions={assist.suggestions}
              skipped={assist.skipped.length}
              listening={running}
            />
          }
        />
      )}
    </section>
  );
}

const STATUS: Record<ReturnType<typeof useAssist>["state"], string> = {
  idle: "Not on a call",
  connecting: "Connecting…",
  listening: "Listening",
  closed: "Call ended",
  refused: "The server declined this connection",
};

function Transcript({
  utterances,
  partial,
  listening,
}: {
  utterances: Utterance[];
  partial: { speaker: "caller" | "agent"; text: string } | null;
  listening: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);

  // Follow the conversation. `block: "nearest"` so it does not drag the whole
  // page down when the panel is already in view.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [utterances.length, partial?.text]);

  return (
    <div className={styles.column}>
      <h2 className={styles.columnTitle}>Transcript</h2>
      <div className={styles.transcript}>
        {utterances.length === 0 && !partial && (
          <p className={styles.quiet}>{listening ? "Waiting for the first word…" : "Nothing yet."}</p>
        )}

        {utterances.map((utterance, index) => (
          <p
            className={`${styles.line} ${utterance.speaker === "agent" ? styles.lineAgent : ""}`}
            key={`${index}-${utterance.text}`}
          >
            <span className={styles.who}>{utterance.speaker === "agent" ? "You" : "Caller"}</span>
            {utterance.text}
          </p>
        ))}

        {partial && (
          // Visibly unsettled: this is a guess that the next fragment will
          // replace, and rendering it like settled text would make the
          // transcript appear to change its mind.
          <p className={`${styles.line} ${styles.linePartial}`}>
            <span className={styles.who}>{partial.speaker === "agent" ? "You" : "Caller"}</span>
            {partial.text}
            <span className={styles.caret} aria-hidden />
          </p>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}

function Suggestions({
  suggestions,
  skipped,
  listening,
}: {
  suggestions: CallSuggestion[];
  skipped: number;
  listening: boolean;
}) {
  // Newest first: the thing just said is the thing being answered.
  const ordered = [...suggestions].reverse();

  return (
    <div className={styles.column}>
      <h2 className={styles.columnTitle}>
        Suggestions
        {skipped > 0 && (
          <span className={styles.skipped} title="Utterances the classifier decided were not questions">
            {skipped} passed over
          </span>
        )}
      </h2>

      <div className={styles.suggestions}>
        {ordered.length === 0 && (
          <p className={styles.quiet}>
            {listening
              ? "Nothing to suggest yet. Most of a call is not a question."
              : "No suggestions."}
          </p>
        )}

        {ordered.map((suggestion) => (
          <article className={styles.suggestion} key={suggestion.id}>
            <header className={styles.suggestionHead}>
              {/* The classifier's own words for why it acted. Shown so the
                  employee can tell it understood the question before they read
                  the answer out to a customer. */}
              <span className={styles.reason}>{suggestion.reason || "worth answering"}</span>
              {suggestion.streaming && <span className={styles.caret} aria-hidden />}
            </header>

            {/* Markdown, and the same renderer the chat uses. The passages are
                written as documents — a procedure is a numbered list and a
                symptom table is a table — so a suggestion built from them has
                markup in it, and rendering it as plain text puts literal "-"
                characters in front of every step. The citation markers become
                chips here for the same reason they do in the chat. */}
            <div className={styles.suggestionText}>
              <Markdown
                content={suggestion.text}
                known={new Set(suggestion.citations.map((citation) => citation.marker))}
                active={null}
                onMarker={() => undefined}
              />
            </div>

            {suggestion.citations.length > 0 && (
              <div className={styles.suggestionCitations}>
                {suggestion.citations.map((citation) => (
                  <span className={styles.suggestionCitation} key={citation.marker}>
                    <span className={styles.marker}>{citation.marker}</span>
                    {citation.documentTitle}
                    {citation.page ? ` · p${citation.page}` : ""}
                  </span>
                ))}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
