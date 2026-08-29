# Security

An internal assistant for a service business holds two things worth protecting:
the company's own procedures and pricing, and its customers' records. The
security list for a system like this is not exotic — employee authentication,
HTTPS, credentials stored securely, role-based access, employees seeing only
what they are authorised to see, a log of AI and API activity, the ability to
disable an account.

Most of that is standard, and the standard implementation is at the bottom of
this page. What is worth reading is the three decisions where the obvious
implementation is subtly wrong.

---

## The three decisions worth arguing about

### 1. Server-side sessions, not JWTs

A JSON Web Token is the reflexive answer and it fails the one requirement this
system cannot compromise on. Disabling an employee account means *now* — someone
has left, or a laptop has gone missing — not at the token's next expiry. A
self-contained token stays valid until it expires no matter what the database
says; making it revocable means checking a denylist on every request, which is a
session lookup with extra steps.

So: an opaque 256-bit token in an HttpOnly cookie, and a `sessions` row.

- Only the SHA-256 of the token is stored, so a database dump contains no
  session anyone can replay. SHA-256 rather than Argon2 on purpose — the input
  is already 256 bits of `secrets` entropy, so there is no dictionary to slow
  down, and this runs on every authenticated request.
- Two expiries. `expires_at` slides forward as the session is used, so an
  employee working a full shift is not logged out mid-question.
  `absolute_expires_at` does not move, so a cookie copied off a machine has a
  shelf life whether or not it keeps being used.
- `user.is_active` is checked on **every** request, not at login. Disabling an
  account also revokes its live sessions in the same transaction, so the browser
  already open on the warehouse floor stops working immediately.
- A **role change** revokes sessions too. The principal — and therefore which
  documents retrieval will consider — is built when the session resolves. A
  demotion that waits for the next login is not a demotion.

[`app/auth/sessions.py`](../services/api/app/auth/sessions.py)

### 2. Role filtering belongs *inside* the retrieval query

This is the one that matters, and it is why the schema looks the way it does
before there is any retrieval code to use it.

If a technician may not read pricing rules, those chunks must never enter the
candidate set. Filtering after retrieval — fetch the top 20, drop the ones the
caller cannot see — looks equivalent and is not: the restricted text has already
been read, and the moment anyone assembles a prompt from that list before the
filter runs, it goes into the model's context and out through its answer. The UI
never shows it. The answer quotes it.

So role is a required argument, not an option:

- `documents.allowed_roles` is the editable source of truth; the same list is
  denormalised onto `chunks`, so the filter is a predicate on the row the vector
  index is already scanning rather than a join that someone will "optimise away"
  later. A dropped join here is a data leak, not a slowdown.
- `Principal.document_roles` is the set of audiences the caller may read.
  `retrieve()` will take the principal as a required argument; there is no
  overload without one.
- On Postgres, **row-level security** enforces the same rule underneath the
  application. Policies read `current_setting('app.role')`, which the API sets
  with `SET LOCAL` on each transaction — `LOCAL` so a pooled connection cannot
  leak one request's identity into the next. `FORCE ROW LEVEL SECURITY` is set,
  because without it the table owner (which is the role the application connects
  as) bypasses its own policies and the exercise protects nobody.

That last layer is the real argument for Postgres over a dedicated vector store.
A filter in application code is one refactor away from being wrong. A policy in
the database is not.

[`app/auth/rbac.py`](../services/api/app/auth/rbac.py) ·
[`alembic/versions/0002_postgres_search_and_rls.py`](../services/api/alembic/versions/0002_postgres_search_and_rls.py)

### 3. The audit trail is not written in the caller's transaction

An authorisation denial has to be recorded even though the request that caused
it is about to roll back. Writing it through the request's session would throw
the record away along with it.

But the first version of this — open a second connection and write immediately —
was correct on Postgres and wrong on SQLite, where the caller still held the
single write lock. Every audited request sat out the five-second busy timeout
and *then* dropped the record. The trail looked fine because failures are
swallowed by design.

Events are now collected on the request and written once, in one transaction,
the moment the database dependency releases its connection. Audit writes never
fail a request; they are logged when they fail, because a trail that quietly
stops is worse than one that was never there.

[`app/audit/log.py`](../services/api/app/audit/log.py)

### 4. Every tool call is recorded where the tools are invoked

The claim in the table below — that one request id ties a request to every
retrieval and connector call made while serving it — was not true for the path
that matters most. The HTTP routes audited their own lookups, but when the
*assistant* opened a customer record on an employee's behalf, all that reached
the trail was one `ai.answer` event at the end of the turn naming which tools
had run. Not which customer. And a turn that failed halfway wrote nothing at
all, having already read the record.

The record is now written in `_invoke`, the single function every tool call
passes through — built-in, MCP, and the ones the model invents. That placement
is the point: a tool added later cannot forget to audit itself, because it was
never the tool's job. Each result names what it read (`resource_type`,
`resource_id`) and the arguments the model sent are kept, capped, alongside it.
A customer's name in an audit record is the record, not a leak in it.

The knowledge-base tool records the document ids it retrieved and the audience
it searched under, which is the difference between "someone asked about
pricing" and "these three documents were read, as a salesperson".

[`app/agent/loop.py`](../services/api/app/agent/loop.py)

### 5. Background work says who it is

Row-level security lets `admin` or `service` write the corpus. Ingestion,
re-indexing and demo seeding all run with no employee signed in — and until
recently none of them said `service` either, so on Postgres they ran as an
unidentified caller and the policies refused the insert. The escape hatch was
described in the migration and implemented nowhere.

`session_scope(service=True)` is now that identity, spelled as a keyword rather
than a role on `Principal`: a role on `Principal` would also be a document
audience, and no employee should be able to become the service by having their
role changed. `tests/test_postgres.py` asserts both halves — that the service
may write, and that a transaction which never said who it was may not.

[`app/db/engine.py`](../services/api/app/db/engine.py)

---

## The rest of the list

| Requirement | How |
|---|---|
| Employee authentication | Argon2id (OWASP parameters, from settings so the cost can be raised — `check_needs_rehash` upgrades each account at its next login) |
| Account enumeration | Unknown and known emails return an identical response, and the hasher runs either way so the latency matches |
| Online password guessing | Per-email *and* per-IP failure counting with a lockout window |
| HTTPS | Cookies are `Secure` when `ENVIRONMENT=production`; TLS terminates at the platform |
| Session hijacking | `HttpOnly`, `SameSite=Lax`, `Secure` in production; token stored hashed |
| Role-based access | One permission table, gates declared on the route, denials audited |
| Employees see only what they may | Role filter inside the retrieval query, plus Postgres RLS |
| Logging of AI/API activity | Append-only `audit_events`; one request id ties a request to every retrieval, connector and model call made while serving it — written at the tool boundary, so it covers what the assistant did as well as what the browser asked for |
| Disabling an account | Flag plus immediate session revocation; the last active administrator cannot be disabled or demoted |
| CRM credential storage | Encrypted at rest, decrypted only in the connector — lands with the Service Fusion milestone |

## Where the boundaries deliberately are

**`users` and `sessions` have no RLS policies.** Authentication has to read those
rows in order to work out who is asking, so a policy keyed on the caller's
identity would be circular. They are reachable only through the auth module.

**The SQLite fallback has no row-level security.** It is the credential-free
demo path, not a deployment target, and it has application-level filtering only.
Saying so is better than implying a guarantee that is not there.

**`X-Forwarded-For` is not trusted for anything that matters.** Behind no proxy
it is caller-controlled, so it feeds throttling and audit context and never an
authorisation decision.

**The web app's permission list is a convenience.** Every permission is enforced
server-side. Hiding a button has never stopped anyone from calling the endpoint
behind it.

## Verifying it

The authorisation boundary is asserted as a matrix in
[`tests/test_rbac.py`](../services/api/tests/test_rbac.py) — every role against
every permission, so the boundary cannot move quietly. The Postgres-only
behaviour is in [`tests/test_postgres.py`](../services/api/tests/test_postgres.py)
and runs against a real `pgvector` container in CI; the workflow fails if those
tests are skipped, because a green suite with the security tests silently
skipped is the worst of both worlds.

```bash
npm run verify        # everything CI runs, fail-fast
```
