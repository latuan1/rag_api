# Local Knowledge Supabase Contract Verification

Date: 2026-06-28

This is the deployment gate for Task 5 in
`docs/plans/2026-06-28-local-knowledge-retrieval-layer.md`. The checks are
read-only and must be run from a trusted backend shell or migration workstation
with access to the production Supabase PostgreSQL database.

## Current Gate Result

Status: pending live production verification.

Workspace findings:

- `RETRIEVAL_DATABASE_URL` is not set in the current shell, so the production
  database contract could not be queried from this workspace.
- No local `supabase/migrations/*.sql` files are present in this checkout, so
  migration SQL could not be inspected here.
- No migration was created or applied. If drift is found during live
  verification, create a reviewed versioned migration under
  `supabase/migrations/*.sql` and apply it through the established deployment
  process only.

## Connection Gate

Before running SQL, confirm that `RETRIEVAL_DATABASE_URL` is a PostgreSQL DSN
copied from Supabase Dashboard database connection settings.

It must not be any of these values:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

Use direct PostgreSQL or Supavisor session mode on port `5432` by default. If
the DSN uses Supavisor transaction mode on port `6543`, set:

```env
RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0
```

## Read-Only Catalog Checks

Run these queries without applying DDL.

```sql
select extname, nspname as extension_schema
from pg_extension
join pg_namespace on pg_namespace.oid = pg_extension.extnamespace
where extname = 'vector';

select to_regclass('public.knowledge_chunks') as knowledge_chunks_table;

select column_name, data_type, udt_schema, udt_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'knowledge_chunks'
order by ordinal_position;

select
  a.attname as column_name,
  pg_catalog.format_type(a.atttypid, a.atttypmod) as formatted_type
from pg_catalog.pg_attribute a
join pg_catalog.pg_class c
  on c.oid = a.attrelid
join pg_catalog.pg_namespace n
  on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname = 'knowledge_chunks'
  and a.attname = 'embedding'
  and a.attnum > 0
  and not a.attisdropped;
```

Expected:

- `vector` exists, currently in the `extensions` schema.
- `public.knowledge_chunks` exists.
- `public.knowledge_chunks.embedding` is `vector(1536)`, formatted as either
  `extensions.vector(1536)` or `vector(1536)` depending on `search_path`.

## RPC Contract Check

```sql
select
  n.nspname as function_schema,
  p.proname as function_name,
  pg_get_function_identity_arguments(p.oid) as arguments,
  pg_get_function_result(p.oid) as result
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname = 'match_knowledge_chunks';
```

Expected arguments:

```text
query_embedding extensions.vector(1536), match_namespace text, match_embedding_profile text, match_threshold double precision, match_count integer, filter_document_type text
```

Expected return columns:

- `chunk_id`
- `document_id`
- `section_id`
- `ordinal`
- `content`
- `embedding_content`
- `policy_title`
- `reference_number`
- `document_type`
- `heading`
- `heading_path`
- `url_source`
- `similarity`

## Active Profile Check

Bind `$1` to the configured `RETRIEVAL_DATASET_NAMESPACE`. The current default
is `vinuni-policy`.

```sql
select
  embedding_profile,
  embedding_provider,
  embedding_model,
  embedding_dimensions,
  embedding_input_version,
  count(*) as active_chunks
from public.knowledge_chunks
where dataset_namespace = $1
  and is_active is true
group by
  embedding_profile,
  embedding_provider,
  embedding_model,
  embedding_dimensions,
  embedding_input_version
order by active_chunks desc, embedding_profile asc;
```

Expected:

- At least one active profile exists.
- Active retrieval dimensions are `1536`.
- The deployed retrieval configuration resolves exactly one active profile for
  the namespace, or sets `RETRIEVAL_EMBEDDING_PROFILE` to the intended active
  profile.

## Drift Handling

If any object is missing or outdated:

1. Create a versioned SQL migration under `supabase/migrations/*.sql`.
2. Review it for forward safety, rollback implications, and destructive
   changes.
3. Apply it first to staging or a disposable database.
4. Re-run this verification.
5. Apply it to production only through the established deployment process.

Do not add startup-time schema creation or alteration to FastAPI, route modules,
or service constructors.

## Deployment Record

Fill this in when the live checks are run:

```text
Verification date:
Operator:
Dataset namespace:
Connection mode: direct | Supavisor session | Supavisor transaction
Database port: 5432 | 6543 | other
Statement cache size:
Contract result: passed | drift found
Migration required: yes | no
Migration path:
Notes:
```
