"use client";

import { useState } from "react";
import type { CustomerRecord, CustomerSummary } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { PageHeader } from "@/components/AppShell";
import { Badge, Button, EmptyState, Input, Skeleton } from "@/components/ui";
import styles from "./customers.module.css";

const EXAMPLES = ["John Smith Portland", "Raman", "(207) 555-0142", "NG-1042"];

export default function CustomersPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CustomerSummary[] | null>(null);
  const [record, setRecord] = useState<CustomerRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 2) return;

    setQuery(trimmed);
    setBusy(true);
    setError(null);
    setRecord(null);
    try {
      const matches = await api.findCustomers(trimmed);
      setResults(matches);
      // One match is unambiguous, so open it. Two or more and the person
      // searching has to choose — which is the reason they typed the town.
      if (matches.length === 1) await open(matches[0]!.id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The lookup failed.");
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setLoadingRecord(true);
    try {
      setRecord(await api.customer(id));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not open that record.");
    } finally {
      setLoadingRecord(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Customers"
        subtitle="Read-only lookups against the CRM — installed equipment, service history with the technician's notes, open estimates and outstanding balances."
        actions={
          <span className={styles.readonly}>
            <Badge>Read-only</Badge>
          </span>
        }
      />

      <form
        className={styles.search}
        onSubmit={(event) => {
          event.preventDefault();
          void search(query);
        }}
      >
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Name, town, phone or account id"
          aria-label="Search customers"
        />
        <Button type="submit" variant="primary" loading={busy}>
          Search
        </Button>
      </form>

      {!results && !busy && (
        <EmptyState
          title="Find a customer"
          body="Search by name, town, phone number or account id. Two customers share a surname in this dataset, so a town narrows it."
          action={
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
              {EXAMPLES.map((example) => (
                <Button key={example} size="small" onClick={() => void search(example)}>
                  {example}
                </Button>
              ))}
            </div>
          }
        />
      )}

      {error && (
        <p className={styles.accountNote} role="alert">
          {error}
        </p>
      )}

      {results && (
        <div className={styles.layout}>
          <div className={styles.results}>
            {results.length === 0 ? (
              <EmptyState title="No match" body={`Nothing in the CRM matches “${query}”.`} />
            ) : (
              results.map((customer) => (
                <button
                  key={customer.id}
                  type="button"
                  className={`${styles.result} ${
                    record?.customer.id === customer.id ? styles.resultActive : ""
                  }`}
                  onClick={() => void open(customer.id)}
                >
                  <div className={styles.resultName}>{customer.name}</div>
                  <div className={styles.resultMeta}>
                    {customer.address} · {customer.phone}
                  </div>
                  <div className={styles.resultMeta}>{customer.id}</div>
                </button>
              ))
            )}
          </div>

          <div>
            {loadingRecord ? (
              <div style={{ display: "grid", gap: 12 }}>
                <Skeleton height={72} />
                <Skeleton height={180} />
              </div>
            ) : record ? (
              <Record record={record} />
            ) : results.length > 1 ? (
              <EmptyState
                title="Two customers match"
                body="Pick one. They are different households — check the town before quoting."
              />
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}

function Record({ record }: { record: CustomerRecord }) {
  const { customer } = record;
  const openEstimates = record.estimates.filter((estimate) => estimate.status === "open");

  return (
    <div className={styles.record}>
      <div className={styles.identity}>
        <div>
          <h2 className={styles.identityName}>{customer.name}</h2>
          <p className={styles.identityMeta}>
            {customer.address}
            <br />
            {customer.phone} · {customer.email}
          </p>
          <p className={styles.identityMeta}>
            {customer.id} · customer since {customer.since}
          </p>
          {customer.notes && <p className={styles.accountNote}>{customer.notes}</p>}
        </div>
        <div className={styles.balance}>
          <div
            className={`${styles.balanceValue} ${
              record.outstandingUsd > 0 ? styles.balanceOwing : ""
            }`}
          >
            ${record.outstandingUsd.toFixed(2)}
          </div>
          <div className={styles.balanceLabel}>outstanding</div>
        </div>
      </div>

      {record.equipment.length > 0 && (
        <section className={styles.section}>
          <h3 className={styles.sectionHead}>Installed equipment</h3>
          {record.equipment.map((item) => (
            <div className={styles.row} key={item.id}>
              <div className={styles.rowTop}>
                <span className={styles.rowTitle}>{item.model}</span>
                <span className={styles.rowMeta}>
                  installed {item.installedOn}
                  {item.warrantyUntil ? ` · warranty to ${item.warrantyUntil}` : ""}
                </span>
              </div>
              <div className={styles.rowMeta}>
                serial {item.serial}
                {item.location ? ` · ${item.location}` : ""}
              </div>
            </div>
          ))}
        </section>
      )}

      {record.jobs.length > 0 && (
        <section className={styles.section}>
          <h3 className={styles.sectionHead}>Service history</h3>
          {record.jobs.map((job) => (
            <div className={styles.row} key={job.id}>
              <div className={styles.rowTop}>
                <span className={styles.rowTitle}>{job.summary}</span>
                <span className={styles.rowMeta}>
                  {job.date} · {job.kind}
                </span>
              </div>
              <div className={styles.rowMeta}>
                {job.id} · {job.technician}
              </div>
              {job.notes && (
                <p className={styles.notes}>
                  <span className={styles.notesLabel}>Technician&rsquo;s notes</span>
                  {job.notes}
                </p>
              )}
            </div>
          ))}
        </section>
      )}

      {openEstimates.length > 0 && (
        <section className={styles.section}>
          <h3 className={styles.sectionHead}>Open estimates</h3>
          {openEstimates.map((estimate) => (
            <div className={styles.row} key={estimate.id}>
              <div className={styles.rowTop}>
                <span className={styles.rowTitle}>{estimate.summary}</span>
                <span className={styles.rowMeta}>${estimate.amountUsd.toFixed(2)}</span>
              </div>
              <div className={styles.rowMeta}>
                {estimate.id} · {estimate.date}
              </div>
            </div>
          ))}
        </section>
      )}

      {record.invoices.length > 0 && (
        <section className={styles.section}>
          <h3 className={styles.sectionHead}>Invoices</h3>
          {record.invoices.map((invoice) => (
            <div className={styles.row} key={invoice.id}>
              <div className={styles.rowTop}>
                <span className={styles.rowTitle}>
                  {invoice.id}{" "}
                  <Badge tone={invoice.balanceUsd > 0 ? "warning" : "success"}>
                    {invoice.status}
                  </Badge>
                </span>
                <span className={styles.rowMeta}>
                  ${invoice.amountUsd.toFixed(2)}
                  {invoice.balanceUsd > 0 ? ` · $${invoice.balanceUsd.toFixed(2)} due` : ""}
                </span>
              </div>
              <div className={styles.rowMeta}>{invoice.date}</div>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
