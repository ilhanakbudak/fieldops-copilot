"use client";

import { useCallback, useEffect, useState } from "react";
import type { Role, UserSummary } from "@fieldops/shared";
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
import styles from "./users.module.css";

const ROLES: Role[] = ["admin", "office", "sales", "technician"];

export default function UsersPage() {
  const { session, can } = useSession();
  const [users, setUsers] = useState<UserSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = can("users:manage");

  const load = useCallback(async () => {
    try {
      setUsers(await api.users());
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load the accounts.");
    }
  }, []);

  useEffect(() => {
    // Not fetched at all without the permission. The request would be refused
    // anyway, and a refusal is an `authz.denied` line in the audit trail —
    // which should mean somebody tried to do something, not that they opened a
    // page they had no button for.
    if (allowed) void load();
  }, [allowed, load]);

  // The API authorises this on every request; hiding the screen is a courtesy,
  // not the control.
  if (!allowed) {
    return (
      <>
        <PageHeader title="Employees" />
        <EmptyState
          title="Not available for your role"
          body="Account administration is limited to administrators."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Employees"
        subtitle="Who can sign in, and what each of them may read. Changing a role or disabling an account also ends that person's current session."
      />

      <div className={styles.layout}>
        <Card>
          <CardHeader
            title="Accounts"
            description={users ? `${users.length} accounts` : undefined}
          />
          {error ? (
            <CardBody>
              <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
                {error}
              </p>
            </CardBody>
          ) : !users ? (
            <CardBody>
              <div style={{ display: "grid", gap: 12 }}>
                <Skeleton height={20} />
                <Skeleton height={20} />
                <Skeleton height={20} />
              </div>
            </CardBody>
          ) : (
            <TableScroll>
              <Table stacked>
                <thead>
                  <tr>
                    <th scope="col">Employee</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Last signed in</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <UserRow
                      key={user.id}
                      user={user}
                      isSelf={user.id === session?.user.id}
                      onChanged={load}
                    />
                  ))}
                </tbody>
              </Table>
            </TableScroll>
          )}
        </Card>

        <div className={styles.invite}>
          <InviteForm onCreated={load} />
        </div>
      </div>
    </>
  );
}

function UserRow({
  user,
  isSelf,
  onChanged,
}: {
  user: UserSummary;
  isSelf: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function apply(patch: Parameters<typeof api.updateUser>[1]) {
    setBusy(true);
    setError(null);
    try {
      await api.updateUser(user.id, patch);
      await onChanged();
    } catch (cause) {
      // The API refuses to let an administrator lock themselves — or everyone —
      // out. That refusal is worth showing rather than swallowing.
      setError(cause instanceof ApiError ? cause.message : "That change was rejected.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td data-label="Employee">
        <div className={styles.name}>
          {user.fullName} {isSelf && <span className={styles.you}>(you)</span>}
        </div>
        <div className={styles.email}>{user.email}</div>
        {error && (
          <div className={styles.email} role="alert" style={{ color: "var(--danger-700)" }}>
            {error}
          </div>
        )}
      </td>
      <td data-label="Role">
        <Select
          className={styles.roleSelect}
          value={user.role}
          disabled={busy || isSelf}
          aria-label={`Role for ${user.fullName}`}
          onChange={(event) => void apply({ role: event.target.value as Role })}
        >
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {role}
            </option>
          ))}
        </Select>
      </td>
      <td data-label="Status">
        <Badge tone={user.isActive ? "success" : "danger"}>
          {user.isActive ? "active" : "disabled"}
        </Badge>
      </td>
      <td data-label="Last signed in">
        <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>
          {user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleDateString() : "never"}
        </span>
      </td>
      <td data-label="Actions">
        <div className={styles.rowActions}>
          <Button
            size="small"
            variant={user.isActive ? "danger" : "secondary"}
            loading={busy}
            disabled={isSelf}
            onClick={() => void apply({ isActive: !user.isActive })}
          >
            {user.isActive ? "Disable" : "Enable"}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function InviteForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("technician");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const created = await api.createUser({ email, fullName, password, role });
      setDone(`${created.email} can now sign in.`);
      setEmail("");
      setFullName("");
      setPassword("");
      await onCreated();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The account was not created.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Add an employee"
        description="They can sign in immediately with the password you set."
      />
      <CardBody>
        <form className={styles.form} onSubmit={submit}>
          <Field label="Full name">
            {(props) => (
              <Input
                {...props}
                required
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                placeholder="Priya Raman"
              />
            )}
          </Field>

          <Field label="Email">
            {(props) => (
              <Input
                {...props}
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="p.raman@example.com"
              />
            )}
          </Field>

          <Field
            label="Temporary password"
            hint="At least 12 characters. Length does more than a character-class rule."
          >
            {(props) => (
              <Input
                {...props}
                type="password"
                required
                minLength={12}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            )}
          </Field>

          <Field label="Role" hint="Decides which documents and tools they can reach.">
            {(props) => (
              <Select
                {...props}
                value={role}
                onChange={(event) => setRole(event.target.value as Role)}
              >
                {ROLES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          {error && (
            <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
              {error}
            </p>
          )}
          {done && (
            <p className={`${styles.notice} ${styles.noticeOk}`} role="status">
              {done}
            </p>
          )}

          <Button type="submit" variant="primary" loading={busy}>
            Create account
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}
