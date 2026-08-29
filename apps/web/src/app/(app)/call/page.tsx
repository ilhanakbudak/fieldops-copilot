"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { CallDemo, CustomerRecord, ScreenPop } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import { Badge, Button, EmptyState } from "@/components/ui";
import { LiveAssist } from "./LiveAssist";
import { useCallStream } from "./useCallStream";
import styles from "./call.module.css";

/**
 * The office's screen while the phone is ringing.
 *
 * The page renders three outcomes and the difference between them is the point:
 * one account opens, several accounts offer a choice, and none still shows the
 * number. See `ScreenPop` in @fieldops/shared.
 */
export default function CallPage() {
  const { can } = useSession();
  const mayAssist = can("calls:assist");
  const { state, pops, dismiss } = useCallStream(mayAssist);
  const demo = useCallDemo(mayAssist);

  if (!mayAssist) {
    return (
      <>
        <PageHeader title="Live call" />
        <EmptyState
          title="Your role does not answer the phone"
          body="Caller lookup is for office staff and administrators. The socket that carries it refuses everyone else, so this is a description of that refusal rather than a hidden button."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Live call"
        subtitle="The phone system posts a webhook the moment a call comes in; the number is matched against the CRM and the record is pushed here over a WebSocket, before anybody picks up."
      />

      <div className={styles.bar}>
        <span className={styles.status}>
          <span className={`${styles.dot} ${dotClass(state)}`} aria-hidden />
          {STATUS_TEXT[state]}
        </span>
        <RingThePhone demo={demo} disabled={state !== "watching"} />
      </div>

      {pops.length === 0 ? (
        <EmptyState
          title="Waiting for a call"
          body="Nothing is ringing. Use one of the numbers above to post a webhook the way a phone system would — through verification, parsing, the CRM lookup and the fan-out, exactly as RingCentral would."
        />
      ) : (
        <div className={styles.pops}>
          {pops.map((pop, index) => (
            <Pop
              key={`${pop.call.callId}-${pop.call.receivedAt}`}
              pop={pop}
              current={index === 0}
              onDismiss={() => dismiss(pop.call.callId)}
            />
          ))}
        </div>
      )}

      <LiveAssist script={demo?.script ?? null} />
    </>
  );
}

const STATUS_TEXT: Record<ReturnType<typeof useCallStream>["state"], string> = {
  connecting: "Connecting to the call stream…",
  watching: "Watching for calls",
  reconnecting: "Connection dropped — retrying",
  refused: "The server declined this connection",
};

function dotClass(state: ReturnType<typeof useCallStream>["state"]): string {
  if (state === "watching") return styles.dotLive ?? "";
  if (state === "refused") return styles.dotRefused ?? "";
  return styles.dotWaiting ?? "";
}

/**
 * Demo mode only. The button posts the webhook *from the browser*, with the
 * token the API handed over, so the path a reviewer clicks is the path a phone
 * system takes — verification included. A button wired straight into the
 * lookup would skip the two steps most worth showing.
 */
function useCallDemo(enabled: boolean): CallDemo | null {
  const [demo, setDemo] = useState<CallDemo | null>(null);

  useEffect(() => {
    if (!enabled) return;
    api
      .callDemo()
      .then(setDemo)
      // Outside demo mode this is a 404 and there is nothing to show. A real
      // deployment has a real phone system and a real transcription service.
      .catch((cause) => {
        if (!(cause instanceof ApiError)) throw cause;
      });
  }, [enabled]);

  return demo;
}

function RingThePhone({ demo, disabled }: { demo: CallDemo | null; disabled: boolean }) {
  const [busy, setBusy] = useState<string | null>(null);

  if (!demo) return null;

  async function ring(number: string) {
    if (!demo) return;
    setBusy(number);
    try {
      await fetch("/api/calls/incoming", {
        method: "POST",
        headers: { "content-type": "application/json", [demo.header]: demo.token },
        body: JSON.stringify({
          event: "call.ringing",
          from: number,
          to: "(207) 555-0100",
          callId: `CALL-${Date.now()}`,
        }),
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.ring}>
      <span className={styles.ringLabel}>Ring from</span>
      {demo.numbers.map((entry) => (
        <Button
          key={entry.number}
          size="small"
          disabled={disabled}
          loading={busy === entry.number}
          onClick={() => void ring(entry.number)}
          title={entry.label}
        >
          {entry.label}
        </Button>
      ))}
    </div>
  );
}

function Pop({
  pop,
  current,
  onDismiss,
}: {
  pop: ScreenPop;
  current: boolean;
  onDismiss: () => void;
}) {
  return (
    <article className={`${styles.pop} ${current ? styles.popCurrent : ""}`}>
      <header className={styles.popHead}>
        <span className={styles.number}>{pop.call.fromNumber}</span>
        {pop.matches.length === 0 && <Badge tone="warning">Not in the CRM</Badge>}
        {pop.matches.length > 1 && <Badge tone="warning">{pop.matches.length} accounts</Badge>}
        {pop.detail && <Badge tone="accent">Account {pop.detail.customer.id}</Badge>}
        <span className={styles.when}>{time(pop.call.receivedAt)}</span>
        <Button size="small" onClick={onDismiss}>
          Dismiss
        </Button>
      </header>

      <div className={styles.popBody}>
        {pop.detail ? (
          <Record record={pop.detail} />
        ) : pop.matches.length > 1 ? (
          <>
            <p className={styles.ambiguous}>
              Two accounts share this line. Nothing has been opened — ask which
              address before pulling a history, because a wrong record does not
              look uncertain while it is being read aloud.
            </p>
            <div className={styles.choices}>
              {pop.matches.map((match) => (
                <Link key={match.id} href={`/customers?id=${match.id}`} className={styles.choice}>
                  <div className={styles.itemTitle}>{match.name}</div>
                  <div className={styles.itemMeta}>
                    {match.address} · {match.id}
                  </div>
                </Link>
              ))}
            </div>
          </>
        ) : (
          <p className={styles.ambiguous}>
            No account has this number. A first-time caller, or one withheld —
            the number is the useful part.
          </p>
        )}
      </div>
    </article>
  );
}

function Record({ record }: { record: CustomerRecord }) {
  const open = record.estimates.filter((estimate) => estimate.status === "open");

  return (
    <>
      <div className={styles.name}>{record.customer.name}</div>
      <div className={styles.meta}>
        {record.customer.address} · customer since {record.customer.since}
      </div>
      {record.outstandingUsd > 0 && (
        <div className={styles.meta}>
          <Badge tone="danger">
            ${record.outstandingUsd.toLocaleString()} outstanding
          </Badge>
        </div>
      )}

      {record.customer.notes && <p className={styles.notes}>{record.customer.notes}</p>}

      <div className={styles.grid}>
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Installed equipment</h3>
          {record.equipment.length === 0 ? (
            <p className={styles.itemMeta}>Nothing on file.</p>
          ) : (
            record.equipment.map((item) => (
              <div className={styles.item} key={item.id}>
                <div className={styles.itemTitle}>{item.model}</div>
                <div className={styles.itemMeta}>
                  {item.serial} · {item.location}
                  {item.warrantyUntil ? ` · warranty to ${item.warrantyUntil}` : ""}
                </div>
              </div>
            ))
          )}
        </section>

        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Recent jobs</h3>
          {record.jobs.length === 0 ? (
            <p className={styles.itemMeta}>No history.</p>
          ) : (
            record.jobs.map((job) => (
              <div className={styles.item} key={job.id}>
                <div className={styles.itemTitle}>
                  {job.date} · {job.summary}
                </div>
                {/* The technician's note, verbatim. It is the most useful thing
                    in the record and the easiest to paraphrase away. */}
                {job.notes && <div className={styles.itemMeta}>{job.notes}</div>}
              </div>
            ))
          )}
        </section>
      </div>

      {open.length > 0 && (
        <section className={styles.section} style={{ marginTop: "var(--space-4)" }}>
          <h3 className={styles.sectionTitle}>Open estimates</h3>
          {open.map((estimate) => (
            <div className={styles.item} key={estimate.id}>
              <div className={styles.itemTitle}>
                ${estimate.amountUsd.toLocaleString()} · {estimate.summary}
              </div>
              <div className={styles.itemMeta}>
                {estimate.id} · {estimate.date}
              </div>
            </div>
          ))}
        </section>
      )}
    </>
  );
}

function time(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
