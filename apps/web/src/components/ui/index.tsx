"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";
import { useId } from "react";
import styles from "./ui.module.css";

function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

/* --- Button ------------------------------------------------------------- */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "default" | "small";
  loading?: boolean;
};

export function Button({
  variant = "secondary",
  size = "default",
  loading = false,
  disabled,
  children,
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cx(styles.button, styles[variant], size === "small" && styles.small, className)}
      disabled={disabled || loading}
      // Screen readers are told the control is busy rather than being left to
      // infer it from a spinner they cannot see.
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className={styles.spinner} aria-hidden />}
      {children}
    </button>
  );
}

/* --- Field -------------------------------------------------------------- */

type FieldProps = {
  label: string;
  hint?: string;
  error?: string | null;
  children: (props: { id: string; "aria-describedby": string | undefined }) => ReactNode;
};

/**
 * Wires a label, a hint and an error message to one control.
 *
 * Done here once rather than at each call site because it is exactly the sort
 * of plumbing that gets skipped: without `aria-describedby` a screen reader
 * announces the field and stops, and the reason the form was rejected is
 * visible only to people who can see it.
 */
export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy })}
      {hint && !error && (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className={styles.errorText} id={errorId} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function Input({
  invalid,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      className={cx(styles.control, invalid && styles.invalid, className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(styles.control, className)} {...rest}>
      {children}
    </select>
  );
}

/* --- Card --------------------------------------------------------------- */

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx(styles.card, className)}>{children}</section>;
}

export function CardHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.cardHeader}>
      <div>
        <h2 className={styles.cardTitle}>{title}</h2>
        {description && <p className={styles.cardDescription}>{description}</p>}
      </div>
      {actions}
    </header>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(styles.cardBody, className)}>{children}</div>;
}

/* --- Badge -------------------------------------------------------------- */

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
  children: ReactNode;
}) {
  const toneClass = {
    neutral: undefined,
    success: styles.badgeSuccess,
    warning: styles.badgeWarning,
    danger: styles.badgeDanger,
    accent: styles.badgeAccent,
  }[tone];

  return <span className={cx(styles.badge, toneClass)}>{children}</span>;
}

/* --- Table -------------------------------------------------------------- */

export function TableScroll({ children }: { children: ReactNode }) {
  return <div className={styles.tableScroll}>{children}</div>;
}

/**
 * `stacked` turns each row into a card below 768px.
 *
 * Every `<td>` must then carry a `data-label`, which is what replaces the
 * header row once it is out of sight.
 */
export function Table({ children, stacked }: { children: ReactNode; stacked?: boolean }) {
  return <table className={cx(styles.table, stacked && styles.stacked)}>{children}</table>;
}

/* --- States ------------------------------------------------------------- */

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      {body && <p className={styles.emptyBody}>{body}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: string }) {
  return <div className={styles.skeleton} style={{ height, width }} aria-hidden />;
}
