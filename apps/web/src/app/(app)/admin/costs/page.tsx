"use client";

import { useCallback, useEffect, useState } from "react";
import type { CostBucket, CostReport } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import { BarSeries } from "@/components/BarSeries";
import { Button, EmptyState, Skeleton, Table, TableScroll } from "@/components/ui";
import styles from "./costs.module.css";

const RANGES = [7, 30, 90] as const;

/**
 * What the assistant has cost, and where.
 *
 * Three breakdowns because "which feature is expensive", "who is asking" and
 * "is it going up" are three questions, and the first answer to each is a
 * different shape. The daily series is the only chart; the other two are
 * rankings, and a ranking of eight rows is a table — a bar chart of it would be
 * a table with the numbers moved somewhere harder to read.
 */
export default function CostsPage() {
  const { can } = useSession();
  const mayRead = can("cost:read");

  const [days, setDays] = useState<number>(30);
  const [report, setReport] = useState<CostReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const load = useCallback(
    async (window: number) => {
      setBusy(true);
      setError(null);
      try {
        setReport(await api.costs(window));
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : "Could not load the report.");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (mayRead) void load(days);
  }, [mayRead, days, load]);

  if (!mayRead) {
    return (
      <>
        <PageHeader title="Cost" />
        <EmptyState
          title="Your role does not see spending"
          body="This page says, in aggregate, what each employee has been asking the assistant about. That is a management view rather than a colleague's, so it needs cost:read."
        />
      </>
    );
  }

  const summary = report?.summary;

  return (
    <>
      <PageHeader
        title="Cost"
        subtitle="Every model call is priced when it is written, never recomputed against today's price list — what a call cost on the day it ran does not change."
      />

      {/* Filters in one row above the charts, so the range applies to
          everything below it and is read once. */}
      <div className={styles.filters} role="group" aria-label="Time range">
        {RANGES.map((range) => (
          <Button
            key={range}
            size="small"
            variant={range === days ? "primary" : undefined}
            aria-pressed={range === days}
            onClick={() => setDays(range)}
          >
            {range} days
          </Button>
        ))}
      </div>

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}

      <div className={styles.tiles}>
        <Tile label="Spend" value={summary ? money(summary.costUsd) : null} busy={busy} />
        <Tile label="Model calls" value={summary ? count(summary.calls) : null} busy={busy} />
        <Tile
          label="Answered from cache"
          value={summary ? count(summary.cacheHits) : null}
          busy={busy}
          hint="Turns served from the semantic cache without calling a model at all"
        />
        <Tile
          label="Prompt cache"
          value={summary ? percent(summary.cachedInputTokens, summary.inputTokens) : null}
          busy={busy}
          hint="Share of input tokens the provider served from its own prompt cache"
        />
      </div>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Spend per day</h2>
        {busy && !report ? (
          <Skeleton height={132} />
        ) : (
          <BarSeries
            title={`Spend per day over the last ${days} days`}
            bars={(report?.byDay ?? []).map((bucket) => ({
              label: bucket.label,
              value: bucket.costUsd,
              caption: shortDate(bucket.label),
            }))}
            format={money}
          />
        )}
      </section>

      <div className={styles.split}>
        <Breakdown
          title="By feature"
          note="A cheap model does query analysis and triage; the good one only writes the answer. This is where that shows."
          rows={report?.byFeature ?? []}
          busy={busy}
          nameOf={featureName}
        />
        <Breakdown
          title="By employee"
          note="Who is asking, in aggregate."
          rows={report?.byUser ?? []}
          busy={busy}
          nameOf={(label) => label}
        />
      </div>
    </>
  );
}

function Tile({
  label,
  value,
  busy,
  hint,
}: {
  label: string;
  value: string | null;
  busy: boolean;
  hint?: string;
}) {
  return (
    <div className={styles.tile}>
      <span className={styles.tileLabel} title={hint}>
        {label}
      </span>
      {value === null && busy ? (
        <Skeleton height={28} width="60%" />
      ) : (
        <span className={styles.tileValue}>{value ?? "—"}</span>
      )}
    </div>
  );
}

function Breakdown({
  title,
  note,
  rows,
  busy,
  nameOf,
}: {
  title: string;
  note: string;
  rows: CostBucket[];
  busy: boolean;
  nameOf: (label: string) => string;
}) {
  const peak = Math.max(...rows.map((row) => row.costUsd), 0);

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{title}</h2>
      <p className={styles.note}>{note}</p>

      {busy && rows.length === 0 ? (
        <Skeleton height={80} />
      ) : rows.length === 0 ? (
        <p className={styles.quiet}>Nothing yet.</p>
      ) : (
        <TableScroll>
          <Table stacked>
            <thead>
              <tr>
                <th scope="col">{title === "By feature" ? "Feature" : "Employee"}</th>
                <th scope="col" className={styles.numeric}>
                  Calls
                </th>
                <th scope="col" className={styles.numeric}>
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label}>
                  <td data-label="Name">
                    <span className={styles.rowName}>{nameOf(row.label)}</span>
                    {/* The bar is the comparison; the number is the value.
                        Both, because a reader scanning for the big one and a
                        reader checking a figure want different things. */}
                    <span
                      className={styles.inlineBar}
                      style={{ width: peak > 0 ? `${(row.costUsd / peak) * 100}%` : "0%" }}
                      aria-hidden
                    />
                  </td>
                  <td data-label="Calls" className={styles.numeric}>
                    {count(row.calls)}
                    {row.cacheHits > 0 && (
                      <span className={styles.cached}> · {count(row.cacheHits)} cached</span>
                    )}
                  </td>
                  <td data-label="Cost" className={styles.numeric}>
                    {money(row.costUsd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableScroll>
      )}
    </section>
  );
}

/** Tool names are for the model; these are for the person reading. */
const FEATURE_LABELS: Record<string, string> = {
  chat: "Chat answers",
  query_analysis: "Query analysis",
  call_assist: "Call suggestions",
  call_triage: "Call triage",
  embedding: "Embeddings",
};

function featureName(label: string): string {
  return FEATURE_LABELS[label] ?? label.replace(/_/g, " ");
}

/**
 * Fractions of a cent are the normal case here and rounding them to two
 * decimals would show most of this page as $0.00 — which reads as "nothing was
 * spent" rather than "a little was".
 */
function money(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function count(value: number): string {
  return value.toLocaleString();
}

function percent(part: number, whole: number): string {
  if (whole <= 0) return "—";
  return `${Math.round((part / whole) * 100)}%`;
}

function shortDate(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00`);
  return Number.isNaN(parsed.valueOf())
    ? iso
    : parsed.toLocaleDateString([], { month: "short", day: "numeric" });
}
