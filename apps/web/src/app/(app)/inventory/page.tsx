"use client";

import { useState } from "react";
import type { Material } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import { Badge, Button, EmptyState, Input, Skeleton } from "@/components/ui";
import styles from "./inventory.module.css";

const EXAMPLES = ["1-inch PEX ball valve", "UV lamp", "brine valve", "sediment cartridge"];

export default function InventoryPage() {
  const { can } = useSession();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Material[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;

    setQuery(trimmed);
    setBusy(true);
    setError(null);
    try {
      setResults(await api.findMaterials(trimmed));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The lookup failed.");
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Inventory"
        subtitle="Where a part is, and how many are left after what is already committed to scheduled jobs."
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
          placeholder="Part name, part number or category"
          aria-label="Search inventory"
        />
        <Button type="submit" variant="primary" loading={busy}>
          Search
        </Button>
      </form>

      {error && <p role="alert">{error}</p>}

      {busy && (
        <div className={styles.results}>
          <Skeleton height={140} />
          <Skeleton height={140} />
        </div>
      )}

      {!results && !busy && !error && (
        <EmptyState
          title="Find a part"
          body="Two valves a size apart sit next to each other on the shelf, so include the size."
          action={
            <div className={styles.examples}>
              {EXAMPLES.map((example) => (
                <Button key={example} size="small" onClick={() => void search(example)}>
                  {example}
                </Button>
              ))}
            </div>
          }
        />
      )}

      {results && !busy && (
        <div className={styles.results}>
          {results.length === 0 ? (
            <EmptyState title="No match" body={`Nothing in the inventory matches “${query}”.`} />
          ) : (
            results.map((material) => (
              <MaterialCard key={material.id} material={material} canSeeCost={can("pricing:read")} />
            ))
          )}
        </div>
      )}
    </>
  );
}

function MaterialCard({ material, canSeeCost }: { material: Material; canSeeCost: boolean }) {
  const countClass =
    material.available === 0
      ? styles.countZero
      : material.belowReorder
        ? styles.countLow
        : undefined;

  return (
    <article className={styles.material}>
      <div className={styles.head}>
        <div>
          <h2 className={styles.name}>{material.name}</h2>
          <p className={styles.sku}>
            {material.sku} · {material.category}
          </p>
          {material.belowReorder && (
            <div style={{ marginTop: 8 }}>
              <Badge tone={material.available === 0 ? "danger" : "warning"}>
                {material.available === 0
                  ? "Out of stock"
                  : `Below reorder point of ${material.reorderPoint}`}
              </Badge>
            </div>
          )}
        </div>
        <div className={styles.count}>
          <div className={`${styles.countValue} ${countClass ?? ""}`}>{material.available}</div>
          <div className={styles.countLabel}>available ({material.unit})</div>
        </div>
      </div>

      <div className={styles.locations}>
        {material.stock.map((location) => (
          <div className={styles.location} key={`${location.warehouse}-${location.bin}`}>
            <div>
              <div className={styles.bin}>{location.bin}</div>
              <div className={styles.where}>
                {location.warehouse} · aisle {location.aisle}, row {location.row}
              </div>
            </div>
            <div className={styles.quantities}>
              {location.onHand} on hand
              {location.committed > 0 && (
                <> · {location.committed} committed · {location.available} free</>
              )}
            </div>
          </div>
        ))}
      </div>

      {material.pricing ? (
        <div className={styles.commercial}>
          <span>
            Supplier <span className={styles.commercialValue}>{material.pricing.supplier}</span>
          </span>
          <span>
            Cost{" "}
            <span className={styles.commercialValue}>
              ${material.pricing.costUsd.toFixed(2)}
            </span>
          </span>
          <span>
            List{" "}
            <span className={styles.commercialValue}>
              ${material.pricing.listUsd.toFixed(2)}
            </span>
          </span>
          <span>
            Lead time{" "}
            <span className={styles.commercialValue}>
              {material.pricing.leadTimeDays} days
            </span>
          </span>
        </div>
      ) : (
        !canSeeCost && (
          <p className={styles.withheld}>
            Supplier and cost are not shown for your role.
          </p>
        )
      )}
    </article>
  );
}
