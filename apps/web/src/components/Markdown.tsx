"use client";

import { Fragment, type ReactNode, isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "@/app/(app)/chat/chat.module.css";

/**
 * Renders an answer as Markdown, with `[S1]` citation markers turned into
 * chips.
 *
 * The awkward part is that the markers live inside the prose, so they have to
 * survive Markdown parsing and then be replaced in whatever element they landed
 * in — a paragraph, a list item, a table cell. `react-markdown` hands each
 * element its children as strings, so the substitution happens in a wrapper
 * applied to every element that can contain text. Pre-processing the Markdown
 * into raw HTML instead would mean enabling `rehype-raw`, which is to say
 * rendering model output as HTML, which is not a trade worth making for a
 * superscript.
 *
 * A marker with no matching citation renders as plain text. That happens when a
 * stored message is reopened and its citations were trimmed — the alternative
 * is a chip that opens nothing.
 */
export function Markdown({
  content,
  known,
  active,
  onMarker,
}: {
  content: string;
  known: Set<string>;
  active: string | null;
  onMarker: (marker: string) => void;
}) {
  const decorate = (children: ReactNode): ReactNode => transform(children, known, active, onMarker);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p>{decorate(children)}</p>,
        li: ({ children }) => <li>{decorate(children)}</li>,
        td: ({ children }) => <td>{decorate(children)}</td>,
        th: ({ children }) => <th>{decorate(children)}</th>,
        strong: ({ children }) => <strong>{decorate(children)}</strong>,
        em: ({ children }) => <em>{decorate(children)}</em>,
        h1: ({ children }) => <h3>{decorate(children)}</h3>,
        h2: ({ children }) => <h3>{decorate(children)}</h3>,
        h3: ({ children }) => <h3>{decorate(children)}</h3>,
        // A wide table scrolls inside the bubble rather than widening the page.
        table: ({ children }) => (
          <div className={styles.tableWrap}>
            <table>{children}</table>
          </div>
        ),
        // Links in an answer point at documents this app has not published yet.
        // Rendering them as text is honest; rendering them as links that 404 is
        // not.
        a: ({ children }) => <>{decorate(children)}</>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

const MARKER = /(\[S\d+\])/g;

function transform(
  children: ReactNode,
  known: Set<string>,
  active: string | null,
  onMarker: (marker: string) => void,
): ReactNode {
  if (typeof children === "string") {
    const parts = children.split(MARKER);
    if (parts.length === 1) return children;

    return parts.map((part, index) => {
      const match = /^\[(S\d+)\]$/.exec(part);
      if (!match || !known.has(match[1]!)) return <Fragment key={index}>{part}</Fragment>;

      const marker = match[1]!;
      return (
        <button
          key={index}
          type="button"
          className={`${styles.marker} ${active === marker ? styles.markerActive : ""}`}
          onClick={() => onMarker(marker)}
          title={`Show source ${marker}`}
        >
          {marker}
        </button>
      );
    });
  }

  if (Array.isArray(children)) {
    return children.map((child, index) => (
      <Fragment key={index}>{transform(child, known, active, onMarker)}</Fragment>
    ));
  }

  // An element — `<code>`, a nested `<strong>`. Left alone: a marker inside a
  // code span is text the author meant literally.
  if (isValidElement(children)) return children;

  return children;
}
