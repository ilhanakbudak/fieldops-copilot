# Infrastructure

Supabase supplies Postgres with `pgvector`, storage for source PDFs, and
row-level security policies — one managed dependency covering what would
otherwise be several services to run, secure and back up.

## Where the schema lives

Alembic, in [`services/api/alembic/`](../services/api/alembic), is the single
source of truth. The same migrations run against Supabase Postgres and against
the local SQLite file; the parts that only Postgres has are guarded inside the
migration rather than kept in a second set of scripts that would drift.

```bash
npm run api:migrate     # apply everything pending
npm run api:seed        # the four demo accounts (demo mode only)
```

| Migration | |
|---|---|
| `0001_initial_schema` | Employees, sessions, documents, chunks, audit and usage events. Portable — identical on both dialects. |
| `0002_postgres_search_and_rls` | Postgres only, skipped on SQLite: the generated `tsvector` column and its GIN index, the HNSW vector index, and the row-level security policies. |

**HNSW rather than IVFFlat** for the vector index: no training step, better
recall at the same probe cost, and the corpus keeps changing as documents are
uploaded — IVFFlat's centroids would need periodic rebuilding to stay accurate.

**A stored generated `tsvector`** rather than `to_tsvector` at query time,
because half the real questions are keyword lookups on a part number or an error
code, and a per-query call over a corpus of manuals can use no index.

## Preparing a Supabase project

Two things need doing once, by a role with more privileges than the application
should ever hold:

```sql
-- 1. Enable the extension. The migration checks for it and moves on rather than
--    demanding a privilege the application role does not have.
create extension if not exists vector;

-- 2. Create the role the application connects as, and let it own the schema it
--    is about to create. Neither SUPERUSER nor BYPASSRLS: either one makes
--    every policy in migration 0002 inert while leaving them visible in the
--    dashboard, which is the worst of both.
create role fieldops_app login password '…';
grant all on schema public to fieldops_app;
```

Then point `DATABASE_URL` at that role and run `npm run api:migrate`.

### Do not use the connection string on the dashboard

Supabase's own `postgres` role carries `BYPASSRLS`. It is not a superuser, so it
looks unremarkable, and the policies are all present when you go and check —
they simply do not apply to it. Every row-level security assertion in
`tests/test_postgres.py` would pass against a project connected that way while
the backstop did nothing.

The service checks at boot and says so, loudly in development and fatally in
production. See [`app/db/health.py`](../services/api/app/db/health.py).

### The application role must own the tables

Supabase installs an `ensure_rls` event trigger that enables row-level security
on every new table in `public`, including `users` and `sessions` — which
migration 0002 deliberately leaves outside the policy set, because
authentication has to read them to work out who is asking.

That is harmless *if* the application role owns those tables: `ENABLE` without
`FORCE` still lets the owner read, and a role holding neither is denied. Which is
what running the migrations as `fieldops_app` gives you.

If you have already migrated as `postgres` and want to switch, reassign first,
or sign-in will start failing with "incorrect email or password" and nothing in
the logs to explain it:

```sql
reassign owned by postgres to fieldops_app;
```

### What migration 0004 takes away

Supabase's default privileges grant `anon`, `authenticated` and `service_role`
full DML on every table created in `public`. That is the right default for a
project where PostgREST *is* the API. It is the wrong one here, where this
application owns its own authentication and connects with its own role.

Left alone it means the *publishable* API key can insert rows into
`audit_events`, whose append policy is permissive on purpose — a failed login
has no authenticated role, and that attempt is exactly the event worth keeping.
A trail the public can forge is not a trail. Migration 0004 revokes those grants
and the default privileges that would hand them back. It is a no-op on a
Postgres without those roles.

> Nothing here is Supabase-specific beyond the connection string: it is
> Postgres, so the same migrations run against Neon, RDS, or a container.

## Running with no accounts at all

Leave `DATABASE_URL` empty. The service uses a local SQLite file, migrates it on
boot and seeds the demo employees, so a fresh clone is a running system. The
trade is stated in [`docs/SECURITY.md`](../docs/SECURITY.md): SQLite has no
row-level security, so that path has application-level filtering only.
