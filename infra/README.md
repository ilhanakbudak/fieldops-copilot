# Infrastructure

Supabase supplies Postgres with `pgvector`, authentication, storage for source
PDFs, and row-level security policies — one managed dependency covering what
would otherwise be four services to run, secure and back up.

```
infra/supabase/migrations/    schema, indexes and RLS policies
```

Migrations land with the data-layer milestone. The shape they will take:

- `pgvector` extension, and an **HNSW** index rather than IVFFlat — no training
  step, better recall, and the corpus changes as documents are uploaded
- A generated `tsvector` column with a GIN index alongside it, because half of
  the real queries are keyword matches on part numbers and error codes
- Row-level security keyed on the employee's role, so a technician cannot read
  pricing rules even if application code forgets to filter

> Nothing here is Supabase-specific beyond the client: it is Postgres, so the
> same migrations run against any Postgres with `pgvector` available.
