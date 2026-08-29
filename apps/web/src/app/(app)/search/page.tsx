"use client";

import { useState } from "react";
import type { SearchResponse } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import { Badge, Button, EmptyState, Input, Skeleton } from "@/components/ui";
import styles from "./search.module.css";

const EXAMPLES = [
  "What does error code E-04 mean?",
  "Why has the water been warm since the radon system was installed?",
  "What is our warranty on a radon water system?",
  "Which treatment removes rotten egg smell?",
  "What is the dealer cost of a radon system?",
];

export default function SearchPage() {
  const { session } = useSession();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;

    setQuery(trimmed);
    setBusy(true);
    setError(null);
    try {
      setResults(await api.search(trimmed, 10));
      setAsked(trimmed);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Search failed.");
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Retrieval"
        subtitle="Raw passages with their similarity scores, and no model in the loop. When an answer is wrong, this is what says whether retrieval found the wrong passage or generation ignored the right one — two failures that look identical from a chat window and have nothing in common as fixes."
      />

      <form
        className={styles.searchBar}
        onSubmit={(event) => {
          event.preventDefault();
          void run(query);
        }}
      >
        <Input
          className={styles.searchInput}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Ask the way an employee would"
          aria-label="Search the knowledge base"
        />
        <Button type="submit" variant="primary" loading={busy}>
          Search
        </Button>
      </form>

      {!results && !busy && !error && (
        <>
          <div className={styles.examples}>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className={styles.example}
                onClick={() => void run(example)}
              >
                {example}
              </button>
            ))}
          </div>
          <p className={styles.note}>
            You are signed in as <strong>{session?.user.role}</strong>. Try the last two as a
            technician and then as sales — the pricing document is not merely hidden from the
            results, it never enters the candidate set.
          </p>
        </>
      )}

      {busy && (
        <div className={styles.results}>
          {[0, 1, 2].map((index) => (
            <div className={styles.hit} key={index}>
              <Skeleton height={18} width="46%" />
              <div style={{ height: 12 }} />
              <Skeleton height={14} />
            </div>
          ))}
        </div>
      )}

      {error && (
        <p className={styles.note} role="alert">
          {error}
        </p>
      )}

      {results && !busy && (
        <>
          <div className={styles.scope}>
            <span>
              {results.hits.length} passage{results.hits.length === 1 ? "" : "s"} for{" "}
              <strong>“{asked}”</strong>
            </span>
            <span aria-hidden>·</span>
            <span>searched:</span>
            {results.searchedRoles.map((role) => (
              <Badge key={role}>{role}</Badge>
            ))}
          </div>

          {results.hits.length === 0 ? (
            <EmptyState
              title="Nothing matched"
              body="Either the corpus has no passage on this, or the documents that do are written for a role you do not hold."
            />
          ) : (
            <div className={styles.results}>
              {results.hits.map((hit) => (
                <article className={styles.hit} key={hit.chunkId}>
                  <div className={styles.hitHeader}>
                    <div>
                      <h2 className={styles.hitTitle}>{hit.documentTitle}</h2>
                      <p className={styles.hitPath}>
                        {hit.docType}
                        {` · ${hit.section ?? "—"}`}
                        {hit.page ? ` · page ${hit.page}` : ""}
                      </p>
                    </div>
                    <div className={styles.score}>
                      <div className={styles.scoreBar}>
                        <div
                          className={styles.scoreFill}
                          style={{ width: `${Math.max(0, Math.min(1, hit.score)) * 100}%` }}
                        />
                      </div>
                      <span className={styles.scoreValue}>{hit.score.toFixed(3)}</span>
                    </div>
                  </div>
                  <p className={styles.snippet}>{hit.content}</p>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
