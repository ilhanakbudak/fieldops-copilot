"use client";

import { useId, useState } from "react";
import styles from "./BarSeries.module.css";

export type Bar = { label: string; value: number; caption?: string };

/**
 * One measure over a sequence of buckets, as bars.
 *
 * Bars rather than a line: daily spend is a set of discrete totals, and a line
 * between them draws a slope that did not happen. Bars rather than an area, for
 * the same reason twice over.
 *
 * One series, so there is no legend — the title names it — and one hue, because
 * every bar is the same *kind* of thing at a different magnitude. A colour per
 * day would be identity encoding on data that has no identities, which is the
 * most common way a chart ends up looking designed and reading as noise.
 *
 * Three details that are not decoration:
 *
 * **The baseline is zero and is not negotiable.** A bar's length *is* its value;
 * starting the axis anywhere else draws a ratio that is not in the data.
 *
 * **Only the largest bar is labelled.** A number on every bar is a table with
 * extra steps. The rest are on hover, and in the table below.
 *
 * **Hover is the default, not an extra.** An SVG chart in a browser is
 * interactive whether or not anybody planned it, and a reader who cannot get
 * the number for the bar they are pointing at will go looking for a CSV.
 */
export function BarSeries({
  bars,
  format,
  title,
  height = 132,
}: {
  bars: Bar[];
  format: (value: number) => string;
  title: string;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const id = useId();

  if (bars.length === 0) {
    return <p className={styles.empty}>Nothing spent in this period.</p>;
  }

  const peak = Math.max(...bars.map((bar) => bar.value), 0);
  const peakIndex = bars.findIndex((bar) => bar.value === peak);
  // A flat run of zeroes must not divide by zero, and must not draw
  // full-height bars either.
  const scale = (value: number) => (peak > 0 ? (value / peak) * 100 : 0);

  return (
    <figure className={styles.figure} aria-labelledby={`${id}-title`}>
      <figcaption id={`${id}-title`} className={styles.srOnly}>
        {title}
      </figcaption>

      <div className={styles.plot} style={{ height }} role="img" aria-label={title}>
        {bars.map((bar, index) => (
          <div
            className={styles.slot}
            key={bar.label}
            onMouseEnter={() => setActive(index)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(index)}
            onBlur={() => setActive(null)}
            tabIndex={0}
            // The bar itself is a few pixels wide on a 90-day range; the hit
            // target is the whole column, which is what a pointer is aiming at.
            aria-label={`${bar.caption ?? bar.label}: ${format(bar.value)}`}
          >
            {(active === index || (active === null && index === peakIndex && peak > 0)) && (
              <span className={styles.value}>{format(bar.value)}</span>
            )}
            <span
              className={`${styles.bar} ${active === index ? styles.barActive : ""}`}
              style={{ height: `${scale(bar.value)}%` }}
            />
          </div>
        ))}
      </div>

      <div className={styles.axis}>
        <span>{bars[0]?.caption ?? bars[0]?.label}</span>
        {bars.length > 1 && <span>{bars[bars.length - 1]?.caption ?? bars[bars.length - 1]?.label}</span>}
      </div>
    </figure>
  );
}
