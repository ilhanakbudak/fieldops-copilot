"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import styles from "./SplitPane.module.css";

const STACK_BELOW = 900;

/**
 * Two panes and a divider the reader can move.
 *
 * A live call is read by two kinds of person. One is watching the transcript
 * and glancing at suggestions; the other is reading a suggestion aloud and
 * glancing at the transcript. There is no split that suits both, so there is no
 * point choosing one for them — which is the whole argument for a divider over
 * a well-chosen fixed percentage.
 *
 * Four things it does that a `<div>` with a mousedown handler does not.
 *
 * **It is a real separator.** `role="separator"` with `aria-valuenow`, focusable,
 * and moved with the arrow keys — Home and End snap to the bounds. A divider
 * that only responds to a drag is furniture that keyboard users cannot touch,
 * and a "resizable layout" nobody can resize without a mouse is not one.
 *
 * **It remembers.** The position is written to `localStorage` under `storageKey`,
 * so somebody who has set it up the way they like does not set it up again
 * every morning. Wrapped in try/catch: private windows throw on access, and a
 * layout preference is not worth a blank page.
 *
 * **It stacks when it is narrow.** Below `STACK_BELOW` the divider is not
 * rendered at all rather than hidden — a separator that is present, focusable
 * and moves nothing is worse than one that is absent. The pane order in the DOM
 * is the reading order when stacked.
 *
 * **It does not thrash while dragging.** The position lives in a CSS custom
 * property set on the element, so a drag is one style write per frame rather
 * than a React render per pixel.
 */
export function SplitPane({
  start,
  end,
  storageKey,
  initial = 50,
  min = 25,
  max = 75,
  startLabel = "First panel",
  endLabel = "Second panel",
}: {
  start: ReactNode;
  end: ReactNode;
  storageKey: string;
  /** Percentage of the width given to `start`. */
  initial?: number;
  min?: number;
  max?: number;
  startLabel?: string;
  endLabel?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(initial);
  const [dragging, setDragging] = useState(false);
  const [stacked, setStacked] = useState(false);
  const id = useId();

  // Read the stored position after mount, not during render: the server has no
  // localStorage, and rendering a different number than the server did is a
  // hydration mismatch.
  useEffect(() => {
    try {
      const stored = Number(window.localStorage.getItem(storageKey));
      if (Number.isFinite(stored) && stored >= min && stored <= max) setSplit(stored);
    } catch {
      // A private window, or site data blocked. The default is fine.
    }
  }, [storageKey, min, max]);

  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${STACK_BELOW - 1}px)`);
    const sync = () => setStacked(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const commit = useCallback(
    (next: number) => {
      const clamped = Math.min(max, Math.max(min, next));
      setSplit(clamped);
      try {
        window.localStorage.setItem(storageKey, String(Math.round(clamped)));
      } catch {
        // Not worth failing a drag over.
      }
    },
    [storageKey, min, max],
  );

  useEffect(() => {
    if (!dragging) return;

    function move(event: PointerEvent) {
      const box = container.current?.getBoundingClientRect();
      if (!box || box.width === 0) return;
      commit(((event.clientX - box.left) / box.width) * 100);
    }
    function stop() {
      setDragging(false);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging, commit]);

  function onKeyDown(event: React.KeyboardEvent) {
    const step = event.shiftKey ? 10 : 2;
    const moves: Record<string, number> = {
      ArrowLeft: -step,
      ArrowRight: step,
    };
    if (event.key in moves) {
      event.preventDefault();
      commit(split + (moves[event.key] ?? 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      commit(min);
    } else if (event.key === "End") {
      event.preventDefault();
      commit(max);
    }
  }

  return (
    <div
      ref={container}
      className={`${styles.split} ${dragging ? styles.dragging : ""}`}
      style={{ "--split": `${split}%` } as React.CSSProperties}
    >
      <section className={styles.pane} aria-label={startLabel} id={`${id}-start`}>
        {start}
      </section>

      {!stacked && (
        <div
          role="separator"
          tabIndex={0}
          aria-orientation="vertical"
          aria-label="Resize the panels"
          aria-controls={`${id}-start`}
          aria-valuenow={Math.round(split)}
          aria-valuemin={min}
          aria-valuemax={max}
          className={styles.divider}
          onPointerDown={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onKeyDown={onKeyDown}
          onDoubleClick={() => commit(initial)}
          title="Drag, or use the arrow keys. Double-click to reset."
        >
          <span className={styles.grip} aria-hidden />
        </div>
      )}

      <section className={styles.pane} aria-label={endLabel}>
        {end}
      </section>
    </div>
  );
}
