import type { ResultRow } from "@fieldops/shared";
import styles from "./ResultRows.module.css";

/**
 * What a tool returned, as labelled rows.
 *
 * The rows are built server-side from whatever JSON a tool server sent — see
 * `services/api/app/agent/render.py` — so this component knows nothing about
 * any particular tool. That is the point: the alternative is a renderer per
 * integration, which is the cost speaking a protocol was supposed to avoid.
 *
 * It is a `<dl>` because that is what this is. A table would need a header row
 * that says "field" and "value", which is two words of furniture for no
 * information, and a `<div>` soup would lose the association a screen reader
 * uses to read a value with its label.
 *
 * `dense` drops it to a single column, for somewhere narrow like a sidebar.
 */
export function ResultRows({ rows, dense }: { rows: ResultRow[]; dense?: boolean }) {
  if (rows.length === 0) return null;

  return (
    <dl className={`${styles.rows} ${dense ? styles.dense : ""}`}>
      {rows.map((row) =>
        row.group ? (
          <div className={styles.group} key={row.label}>
            <dt className={styles.groupLabel}>{row.label}</dt>
            <dd className={styles.groupBody}>
              <ResultRows rows={row.group} dense />
            </dd>
          </div>
        ) : (
          <div className={styles.row} key={row.label}>
            <dt className={styles.label}>{row.label}</dt>
            <dd className={styles.value}>{row.value}</dd>
          </div>
        ),
      )}
    </dl>
  );
}
