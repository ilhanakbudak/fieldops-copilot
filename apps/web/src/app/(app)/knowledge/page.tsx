"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentSummary, Role } from "@fieldops/shared";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { PageHeader } from "@/components/AppShell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  Table,
  TableScroll,
} from "@/components/ui";
import { UploadIcon } from "@/components/icons";
import styles from "./knowledge.module.css";

const ROLES: Role[] = ["admin", "office", "sales", "technician"];

const DOC_TYPES = [
  { value: "manual", label: "Equipment manual" },
  { value: "sop", label: "Standard operating procedure" },
  { value: "warranty", label: "Warranty policy" },
  { value: "pricing", label: "Pricing" },
  { value: "guide", label: "Troubleshooting guide" },
  { value: "faq", label: "FAQ" },
];

function statusTone(status: string) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "danger" as const;
  return "warning" as const;
}

export default function KnowledgePage() {
  const { can } = useSession();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDocuments(await api.documents());
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the library.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canManage = can("documents:manage");

  return (
    <>
      <PageHeader
        title="Documents"
        subtitle={
          canManage
            ? "Manuals, procedures and policies the assistant answers from. Each document is tagged with the roles it is written for, and that tag is what the retrieval query filters on."
            : "Manuals, procedures and policies the assistant answers from. You see the documents written for your role."
        }
      />

      <div className={styles.layout}>
        <Card>
          <CardHeader
            title="Library"
            description={
              documents
                ? `${documents.length} document${documents.length === 1 ? "" : "s"} visible to you`
                : undefined
            }
          />
          {error ? (
            <CardBody>
              <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
                {error}
              </p>
            </CardBody>
          ) : !documents ? (
            <CardBody>
              <div style={{ display: "grid", gap: 12 }}>
                <Skeleton height={20} />
                <Skeleton height={20} />
                <Skeleton height={20} />
              </div>
            </CardBody>
          ) : documents.length === 0 ? (
            <EmptyState
              title="Nothing here yet"
              body={
                canManage
                  ? "Upload a manual, an SOP or a policy. PDF, Markdown and plain text are supported."
                  : "No documents have been tagged for your role yet."
              }
            />
          ) : (
            <TableScroll>
              <Table stacked>
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    <th scope="col">Type</th>
                    <th scope="col">Audience</th>
                    <th scope="col">Indexed</th>
                    <th scope="col">Status</th>
                    {canManage && <th scope="col" />}
                  </tr>
                </thead>
                <tbody>
                  {documents.map((document) => (
                    <DocumentRow
                      key={document.id}
                      document={document}
                      canManage={canManage}
                      onChanged={load}
                    />
                  ))}
                </tbody>
              </Table>
            </TableScroll>
          )}
        </Card>

        {canManage && (
          <div className={styles.uploader}>
            <Uploader onUploaded={load} />
          </div>
        )}
      </div>
    </>
  );
}

function DocumentRow({
  document,
  canManage,
  onChanged,
}: {
  document: DocumentSummary;
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  async function remove() {
    // A native confirm is not elegant. It is also unambiguous, keyboard
    // accessible for free, and impossible to dismiss by accident — which for a
    // destructive action beats a prettier dialog.
    if (!window.confirm(`Delete “${document.title}” and its ${document.chunkCount} chunks?`)) {
      return;
    }
    setBusy(true);
    await api.remove(document.id).catch(() => undefined);
    await onChanged();
    setBusy(false);
  }

  return (
    <tr>
      <td data-label="Document">
        <div className={styles.title}>{document.title}</div>
        <div className={styles.filename}>{document.sourceFilename}</div>
      </td>
      <td data-label="Type">
        <Badge>{document.docType}</Badge>
      </td>
      <td data-label="Audience">
        <div className={styles.roleList}>
          {document.allowedRoles.map((role) => (
            <Badge key={role}>{role}</Badge>
          ))}
        </div>
      </td>
      <td data-label="Indexed" className={`${styles.meta} tabular`}>
        {document.chunkCount} chunk{document.chunkCount === 1 ? "" : "s"}
        {document.pageCount ? ` · ${document.pageCount}p` : ""}
      </td>
      <td data-label="Status">
        <Badge tone={statusTone(document.status)}>{document.status}</Badge>
        {document.error && (
          <div className={styles.filename} title={document.error}>
            {document.error.slice(0, 60)}
          </div>
        )}
      </td>
      {canManage && (
        <td data-label="Actions">
          <div className={styles.rowActions}>
            <Button variant="danger" size="small" onClick={remove} loading={busy}>
              Delete
            </Button>
          </div>
        </td>
      )}
    </tr>
  );
}

function Uploader({ onUploaded }: { onUploaded: () => Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("manual");
  const [roles, setRoles] = useState<Role[]>(["technician"]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function accept(next: File | null) {
    setFile(next);
    setError(null);
    setResult(null);
    // Filling the title from the filename is right most of the time and always
    // editable — an empty required field the user must invent a value for is
    // friction for no benefit.
    if (next && !title) {
      setTitle(next.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " "));
    }
  }

  function toggleRole(role: Role) {
    setRoles((current) =>
      current.includes(role) ? current.filter((value) => value !== role) : [...current, role],
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose a file first.");
      return;
    }
    if (roles.length === 0) {
      setError("Pick at least one role, or nobody will be able to find it.");
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.upload({ file, title, docType, roles });
      const blanks = response.pagesWithoutText.length;
      setResult(
        `Indexed ${response.chunks} chunks from ${response.pages} page${
          response.pages === 1 ? "" : "s"
        } in ${(response.durationMs / 1000).toFixed(1)}s` +
          (blanks ? ` · ${blanks} page${blanks === 1 ? "" : "s"} had no text layer` : ""),
      );
      setFile(null);
      setTitle("");
      if (inputRef.current) inputRef.current.value = "";
      await onUploaded();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Add a document" description="PDF, Markdown or plain text." />
      <CardBody>
        <form className={styles.form} onSubmit={submit}>
          <div
            className={`${styles.fileZone} ${dragging ? styles.fileZoneActive : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                inputRef.current?.click();
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              accept(event.dataTransfer.files[0] ?? null);
            }}
          >
            <UploadIcon />
            {file ? (
              <span className={styles.fileZoneName}>{file.name}</span>
            ) : (
              <>
                <span className={styles.fileZoneName}>Drop a file or browse</span>
                <span className={styles.fileZoneHint}>.pdf · .md · .txt · up to 40 MB</span>
              </>
            )}
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,.md,.markdown,.txt"
              className="visually-hidden"
              onChange={(event) => accept(event.target.files?.[0] ?? null)}
            />
          </div>

          <Field label="Title">
            {(props) => (
              <Input
                {...props}
                required
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="NG-4200 Service Manual"
              />
            )}
          </Field>

          <Field label="Type">
            {(props) => (
              <Select
                {...props}
                value={docType}
                onChange={(event) => setDocType(event.target.value)}
              >
                {DOC_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <div>
            <p style={{ fontSize: "var(--text-sm)", fontWeight: 500, color: "var(--text-secondary)", marginBottom: "var(--space-2)" }}>
              Who can read it
            </p>
            <div className={styles.roleGrid} role="group" aria-label="Audience">
              {ROLES.map((role) => (
                <label
                  key={role}
                  className={`${styles.roleToggle} ${roles.includes(role) ? styles.roleToggleOn : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={roles.includes(role)}
                    onChange={() => toggleRole(role)}
                  />
                  {role}
                </label>
              ))}
            </div>
          </div>

          {error && (
            <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
              {error}
            </p>
          )}
          {result && (
            <p className={`${styles.notice} ${styles.noticeOk}`} role="status">
              {result}
            </p>
          )}

          <Button type="submit" variant="primary" loading={busy}>
            {busy ? "Indexing" : "Upload and index"}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}
