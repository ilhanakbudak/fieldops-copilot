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

-- 2. Create the role the application connects as. Not a superuser, and not the
--    owner of the database: a superuser bypasses row-level security even with
--    FORCE, which would silently disable the policies in migration 0002.
create role fieldops_app login password '…';
grant all on schema public to fieldops_app;
```

Then point `DATABASE_URL` at that role and run `npm run api:migrate`.

> Nothing here is Supabase-specific beyond the connection string: it is
> Postgres, so the same migrations run against Neon, RDS, or a container.

## Running with no accounts at all

Leave `DATABASE_URL` empty. The service uses a local SQLite file, migrates it on
boot and seeds the demo employees, so a fresh clone is a running system. The
trade is stated in [`docs/SECURITY.md`](../docs/SECURITY.md): SQLite has no
row-level security, so that path has application-level filtering only.
