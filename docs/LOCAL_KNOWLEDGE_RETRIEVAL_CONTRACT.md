# Local Knowledge Retrieval Contract

**Status:** Authoritative retrieval-layer contract for the production Supabase PostgreSQL knowledge database with pgvector  
**Audience:** Trusted backend services, application servers, evaluation jobs, and local tools that retrieve from the ingested VinUniversity policy knowledge base  
**Database schema:** `public.knowledge_chunks` with `public.match_knowledge_chunks(...)`  
**Default dataset namespace:** `vinuni-policy`  
**Embedding profile:** `{embedding_provider}:{embedding_model}:1536:v1`

This document describes how an external backend or application can build a retrieval layer over the ingested production Supabase PostgreSQL knowledge database with pgvector without reading the ingestion source code.

It is intentionally consumer-facing. It documents the stable database contract, retrieval RPC, row semantics, security expectations, and compatibility rules. Ingestion operator details such as chunk generation, cache refreshes, and provider retry behavior are outside this contract except where they affect retrieval compatibility.

## 1. Security Boundary

Retrieval must run from a trusted server-side environment.

Do not expose any of the following to browsers, mobile clients, desktop clients, or untrusted user-controlled code:

- PostgreSQL owner credentials.
- `DATABASE_URL`.
- Supabase `service_role` keys.
- Raw database connection strings.
- Raw embedding vectors unless the product explicitly needs internal diagnostics.

The migration enables row level security on `public.knowledge_chunks`, revokes table access from `anon` and `authenticated`, revokes public execution of the retrieval function, and grants `public.match_knowledge_chunks(...)` execution only to `service_role`.

Browser-facing applications should call their own backend API. That backend can then generate a query embedding and call the production Supabase PostgreSQL database with pgvector using server-held credentials.

## 2. Required Runtime Configuration

A retrieval service needs the same namespace and embedding profile that ingestion used.

Recommended retrieval-project configuration names. These example values describe the currently observed VinUni deployment:

```env
RETRIEVAL_DATABASE_URL=
RETRIEVAL_EMBEDDING_API_KEY=
RETRIEVAL_EMBEDDING_BASE_URL=
RETRIEVAL_EMBEDDING_PROVIDER=google-gemini
RETRIEVAL_EMBEDDING_MODEL=gemini-embedding-001
RETRIEVAL_EMBEDDING_DIMENSIONS=1536
RETRIEVAL_DATASET_NAMESPACE=vinuni-policy
RETRIEVAL_EMBEDDING_PROFILE=google-gemini:gemini-embedding-001:1536:v1
```

These names are recommendations for retrieval projects. They are not required by the database. Whatever names a consuming project chooses, it must carry these values into embedding generation and database calls:

| Setting | Value |
|---|---|
| Database connection | Server-side Supabase PostgreSQL service connection with pgvector |
| Dataset namespace | `vinuni-policy` for the current VinUni policy dataset |
| Embedding provider | Provider from the active stored profile |
| Embedding model | Model from the active stored profile |
| Embedding dimensions | `1536` |
| Embedding input version | `v1` |
| Embedding profile | `{embedding_provider}:{embedding_model}:1536:v1` |

The query embedding must be generated with the same provider, model, dimensions, and input version as the active ingested profile. Do not query a profile with embeddings from another model or dimension count.

The current database schema supports exactly `1536` dimensions:

- `public.knowledge_chunks.embedding` is `extensions.vector(1536)`.
- `public.match_knowledge_chunks.query_embedding` is `extensions.vector(1536)`.
- `public.knowledge_chunks.embedding_dimensions` has a check constraint requiring `1536`.

If an ingestion run uses another provider, model, dimension count, or embedding input version, it uses a different `embedding_profile`. Retrieval consumers must choose the exact active profile they intend to query.

### 2.1 Observed Local Embedding Profile

The ingestion implementation uses the OpenAI Python SDK with an OpenAI-compatible embeddings endpoint.

The current local ingestion artifacts show the following embedding configuration:

| Field | Observed value |
|---|---|
| Provider compatibility | OpenAI-compatible embeddings API |
| Provider | `google-gemini` |
| Model | `gemini-embedding-001` |
| Dimensions | `1536` |
| Input version | `v1` |
| Dataset namespace | `vinuni-policy` |
| Observed profile | `google-gemini:gemini-embedding-001:1536:v1` |

Verification source: `data/ingesting/embedding_cache.jsonl` contains 458 cached embeddings for the profile `google-gemini:gemini-embedding-001:1536:v1`.

This verifies the local embedding cache and current ingestion configuration only. It does not independently verify that this profile is currently active in PostgreSQL. A consuming retrieval service should query `public.knowledge_chunks` to discover and verify the active profile before serving retrieval traffic.

### 2.2 OpenAI-Compatible Provider Profiles

The ingestion client uses the OpenAI SDK interface, but the provider and model are configuration, not a retrieval-layer constant. It can target the SDK default endpoint or an OpenAI-compatible embeddings API by setting `INGEST_EMBEDDING_BASE_URL`.

Ingestion produced the stored profile using these rules:

| Ingestion setting | Retrieval impact |
|---|---|
| `INGEST_EMBEDDING_BASE_URL` | When set, requests are sent to that OpenAI-compatible API base URL instead of the SDK default endpoint. |
| `INGEST_EMBEDDING_PROVIDER` | Optional stable provider slug. If unset while `INGEST_EMBEDDING_BASE_URL` is set, the stored provider identity defaults to `openai-compatible`; if both are unset, the code uses its built-in provider default. |
| `INGEST_EMBEDDING_MODEL` | Model name sent to the compatible provider and stored in the profile. |
| `INGEST_EMBEDDING_DIMENSIONS` | Must remain `1536` for the current database schema. |
| Embedding input version | Fixed at `v1` in the current ingestion code. |

The stored profile format is:

```text
{embedding_provider}:{embedding_model}:1536:v1
```

Examples:

| Ingestion configuration | Stored `embedding_profile` |
|---|---|
| SDK default endpoint with configured provider and model | `{provider}:{model}:1536:v1` |
| Compatible provider without explicit provider slug | `openai-compatible:{model}:1536:v1` |
| Compatible provider with `INGEST_EMBEDDING_PROVIDER=<provider>` and `INGEST_EMBEDDING_MODEL=<model>` | `<provider>:<model>:1536:v1` |

Retrieval clients must use the exact `embedding_profile` stored in `public.knowledge_chunks.embedding_profile`. Do not hardcode an embedding provider or model in retrieval clients.

To discover active profiles for a namespace:

```sql
select
  embedding_profile,
  embedding_provider,
  embedding_model,
  embedding_dimensions,
  count(*) as active_chunks
from public.knowledge_chunks
where dataset_namespace = $1
  and is_active is true
group by embedding_profile, embedding_provider, embedding_model, embedding_dimensions
order by active_chunks desc, embedding_profile asc;
```

## 3. Database Object Contract

### 3.1 Table

Retrieval rows are stored in:

```sql
public.knowledge_chunks
```

Primary key:

```text
(dataset_namespace, embedding_profile, chunk_id)
```

Only active rows are part of the current retrieval snapshot:

```sql
is_active is true
```

Always scope direct reads by all of these values:

```sql
dataset_namespace = $namespace
embedding_profile = $profile
is_active is true
```

### 3.2 Retrieval-Relevant Columns

Identity and ordering:

| Column | Type | Semantics |
|---|---|---|
| `dataset_namespace` | `text` | Dataset partition, default `vinuni-policy`. |
| `embedding_profile` | `text` | Provider/model/dimensions/input-version identity. |
| `chunk_id` | `text` | Stable chunk identifier within namespace/profile. |
| `document_id` | `text` | Stable source document key. |
| `section_id` | `text` | Stable source section identifier. |
| `ordinal` | `integer` | Zero-based chunk order within `document_id`. |

Content:

| Column | Type | Semantics |
|---|---|---|
| `content` | `text` | Source-derived Markdown for answers, citations, and display. |
| `embedding_content` | `text` | Exact deterministic text embedded during ingestion. Use for diagnostics, not as the default answer text. |
| `content_kind` | `text` | Retrieval/content classification such as `policy_text`, `table`, `table_record`, `reference_link`, or `image_reference`. |

Policy metadata:

| Column | Type | Semantics |
|---|---|---|
| `policy_title` | `text` | Human-readable policy title. |
| `reference_number` | `text null` | Policy code or reference number when available. |
| `document_type` | `text null` | Policy-status document type, for example `Policy`. |
| `issuing_by` | `text null` | Issuing authority when available. |
| `issuing_date` | `text null` | Normalized issuing date when available. |
| `applying_for` | `text null` | Audience/scope when available. |
| `security_classification` | `text null` | Source security classification when available. |
| `url_source` | `text` | Source policy URL. |

Structure and provenance:

| Column | Type | Semantics |
|---|---|---|
| `heading` | `text` | Primary heading for the chunk. |
| `heading_path` | `jsonb` | Array of heading strings from document root to the chunk heading. |
| `parent_heading_path` | `jsonb` | `heading_path` without the final item. |
| `source_block_indices` | `jsonb` | Array of source manifest block indexes. |
| `source_line_spans` | `jsonb` | Array of line-span objects showing contributing source ranges. |
| `block_types` | `jsonb` | Array of contributing block-type strings. |
| `image_references` | `jsonb` | Array of associated image metadata objects. |

Quality signals:

| Column | Type | Semantics |
|---|---|---|
| `review_required` | `boolean` | True when source quality or transformation diagnostics need human review. This is not a deletion flag. |
| `review_reasons` | `jsonb` | Array of machine-readable review reasons. |
| `data_quality_flags` | `jsonb` | Array of source quality flags. |
| `warnings` | `jsonb` | Array of ingestion or chunking warnings. |

Versioning and hashes:

| Column | Type | Semantics |
|---|---|---|
| `configuration_fingerprint` | `text` | Hash of behavior-affecting chunking configuration. |
| `strategy_version` | `text` | Chunking strategy version. |
| `schema_version` | `text` | Chunk schema version, currently `chunk.v1`. |
| `content_sha256` | `text` | Hash of display `content`. |
| `embedding_input_sha256` | `text` | Hash of `embedding_content` plus embedding profile inputs. |
| `metadata_sha256` | `text` | Hash of retrieval-relevant metadata. |

Embedding metadata:

| Column | Type | Semantics |
|---|---|---|
| `embedding_provider` | `text` | Provider identity used for the ingested profile. |
| `embedding_model` | `text` | Embedding model used for the ingested profile. |
| `embedding_dimensions` | `integer` | Always `1536` in the current schema. |
| `embedding_input_version` | `text` | Embedding input template version, default `v1`. |
| `embedding` | `extensions.vector(1536)` | Stored chunk embedding. |

Lifecycle and audit:

| Column | Type | Semantics |
|---|---|---|
| `is_active` | `boolean` | True when the row belongs to the current snapshot for its namespace/profile. |
| `dataset_run_id` | `uuid` | Ingestion run that last inserted, updated, reactivated, or deactivated the row. |
| `last_seen_run_id` | `uuid null` | Audit metadata from ingestion refreshes. Do not use for retrieval filtering. |
| `created_at` | `timestamptz` | Database creation timestamp. |
| `updated_at` | `timestamptz` | Database update timestamp. |

### 3.3 JSONB Shapes

`heading_path` and `parent_heading_path`:

```json
["Academic Regulations for Doctoral Degree Program", "Article 1. Scope"]
```

`source_block_indices`:

```json
[42, 43, 44]
```

`source_line_spans`:

```json
[
  {"line_start": 210, "line_end": 210, "role": "repeated_heading"},
  {"line_start": 212, "line_end": 239, "role": "primary"}
]
```

Allowed source span roles in the current chunk contract are:

```text
primary
repeated_heading
repeated_local_anchor
overlap
image_association
repeated_table_header
```

`block_types`, `review_reasons`, `data_quality_flags`, and `warnings` are arrays of strings. `image_references` is an array of objects; current image reference objects may include:

```json
{
  "url": "https://example.edu/image.png",
  "alt_text": "Policy image",
  "source_block_index": 78,
  "line_start": 330,
  "line_end": 330
}
```

## 4. Primary Retrieval Interface

Use the database function:

```sql
public.match_knowledge_chunks(
  query_embedding extensions.vector(1536),
  dataset_namespace text,
  embedding_profile text,
  match_threshold float,
  match_count int,
  filter_document_type text default null
)
```

The function returns:

| Column | Type | Semantics |
|---|---|---|
| `chunk_id` | `text` | Matching chunk ID. |
| `document_id` | `text` | Source document ID. |
| `section_id` | `text` | Source section ID. |
| `ordinal` | `integer` | Chunk order within the source document. |
| `content` | `text` | Display/citation text. |
| `embedding_content` | `text` | Text that was embedded during ingestion. |
| `policy_title` | `text` | Source policy title. |
| `reference_number` | `text null` | Policy reference number when available. |
| `document_type` | `text null` | Policy document type when available. |
| `heading` | `text` | Primary chunk heading. |
| `heading_path` | `jsonb` | Full heading path. |
| `url_source` | `text` | Source policy URL. |
| `similarity` | `float` | `1.0 - cosine_distance` from the query vector. |

The function applies these rules:

- It only returns rows where `is_active is true`.
- It filters to the supplied `dataset_namespace`.
- It filters to the supplied `embedding_profile`.
- It applies `filter_document_type` only when that argument is not null.
- It rejects `match_threshold` values outside `[-1.0, 1.0]`.
- It clamps `match_count` to the inclusive range `1..50`.
- It sorts by vector cosine distance, then `chunk_id` ascending for deterministic ties.

Recommended starting values:

| Input | Recommended starting value |
|---|---|
| `match_count` | `5` or `10` |
| `match_threshold` | `0.0` for broad recall, tune upward only after evaluation |
| `filter_document_type` | `null` unless the product has a specific policy-type constraint |

### 4.1 SQL Examples

Configured profile:

```sql
select *
from public.match_knowledge_chunks(
  $1::extensions.vector(1536),
  'vinuni-policy',
  '<provider>:<model>:1536:v1',
  0.0,
  10,
  null
);
```

OpenAI-compatible provider without an explicit provider slug:

```sql
select *
from public.match_knowledge_chunks(
  $1::extensions.vector(1536),
  'vinuni-policy',
  'openai-compatible:<model>:1536:v1',
  0.0,
  10,
  null
);
```

The `$1` value must be a 1536-value query embedding generated by the same provider/model represented by the supplied `embedding_profile`.

When using `pgvector` client bindings, pass the vector using the binding's native vector support. If passing text SQL literals, use a pgvector-compatible literal such as:

```text
[0.0123,-0.0456,...]
```

Do not build vector SQL literals from untrusted text. Prefer parameterized queries.

### 4.2 Supabase RPC Example

Call the RPC from a trusted server with a Supabase `service_role` key.

Configured profile:

```ts
const { data, error } = await supabase.rpc("match_knowledge_chunks", {
  query_embedding: queryEmbedding,
  dataset_namespace: "vinuni-policy",
  embedding_profile: "<provider>:<model>:1536:v1",
  match_threshold: 0.0,
  match_count: 10,
  filter_document_type: null,
});

if (error) {
  throw error;
}
```

OpenAI-compatible provider profile:

```ts
const { data, error } = await supabase.rpc("match_knowledge_chunks", {
  query_embedding: queryEmbedding,
  dataset_namespace: "vinuni-policy",
  embedding_profile: "openai-compatible:<model>:1536:v1",
  match_threshold: 0.0,
  match_count: 10,
  filter_document_type: null,
});

if (error) {
  throw error;
}
```

The exact client-side representation of `query_embedding` depends on the Supabase/PostgREST and pgvector support available in the backend runtime. If array-to-vector serialization is not supported by the client, call PostgreSQL directly with a server-side driver that supports pgvector, or use a parameterized SQL function call through a trusted backend.

## 5. Query Embedding Requirements

For any profile, generate query embeddings with the same provider and model used during ingestion:

```text
provider: <provider>
model: <model>
dimensions: 1536
input version: v1
profile: <provider>:<model>:1536:v1
```

If ingestion did not set a provider slug, use the resolved provider identity stored in the database profile:

```text
provider: <resolved-provider>
model: <model>
dimensions: 1536
input version: v1
profile: <resolved-provider>:<model>:1536:v1
```

The query text does not need to use the chunk `embedding_content` template. User questions can be embedded directly, for example:

```text
What is the grade appeal procedure?
```

Compatibility requirement: the resulting vector must have exactly 1536 finite numeric values and must come from the same embedding profile as the ingested rows being searched. The profile string is the contract; a retrieval backend should discover or configure it explicitly instead of assuming any provider or model.

## 6. Complete Python Retrieval Example

This example uses recommended `RETRIEVAL_*` environment variable names. A consuming project may use different names, but the values must resolve to the active stored profile and its matching embedding provider/model.

```python
import os
from typing import Any

import psycopg
from openai import OpenAI
from pgvector.psycopg import register_vector


def embed_query(query: str) -> list[float]:
    client = OpenAI(
        api_key=os.environ["RETRIEVAL_EMBEDDING_API_KEY"],
        base_url=os.environ.get("RETRIEVAL_EMBEDDING_BASE_URL") or None,
    )
    dimensions = int(os.environ.get("RETRIEVAL_EMBEDDING_DIMENSIONS", "1536"))
    response = client.embeddings.create(
        model=os.environ["RETRIEVAL_EMBEDDING_MODEL"],
        input=[query],
        dimensions=dimensions,
    )
    vector = response.data[0].embedding
    if len(vector) != dimensions:
        raise ValueError(f"query embedding must have {dimensions} dimensions")
    return [float(value) for value in vector]


def retrieve(query: str, match_count: int = 10, match_threshold: float = 0.0) -> list[dict[str, Any]]:
    query_vector = embed_query(query)
    namespace = os.environ.get("RETRIEVAL_DATASET_NAMESPACE", "vinuni-policy")
    profile = os.environ["RETRIEVAL_EMBEDDING_PROFILE"]

    with psycopg.connect(os.environ["RETRIEVAL_DATABASE_URL"]) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  chunk_id,
                  document_id,
                  section_id,
                  ordinal,
                  content,
                  embedding_content,
                  policy_title,
                  reference_number,
                  document_type,
                  heading,
                  heading_path,
                  url_source,
                  similarity
                from public.match_knowledge_chunks(
                  %s,
                  %s,
                  %s,
                  %s,
                  %s,
                  %s
                )
                """,
                (
                    query_vector,
                    namespace,
                    profile,
                    match_threshold,
                    match_count,
                    None,
                ),
            )
            columns = [description.name for description in cur.description]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


if __name__ == "__main__":
    for result in retrieve("What is the grade appeal procedure?"):
        print(
            result["similarity"],
            result["policy_title"],
            result["heading"],
            result["url_source"],
        )
```

For the current VinUni profile, configure:

```env
RETRIEVAL_EMBEDDING_PROVIDER=google-gemini
RETRIEVAL_EMBEDDING_MODEL=gemini-embedding-001
RETRIEVAL_EMBEDDING_DIMENSIONS=1536
RETRIEVAL_DATASET_NAMESPACE=vinuni-policy
RETRIEVAL_EMBEDDING_PROFILE=google-gemini:gemini-embedding-001:1536:v1
```

## 7. Optional Backend-Only Direct Reads

The RPC is the primary semantic retrieval interface. Direct table reads are useful for citations, neighbor expansion, document views, diagnostics, and post-filtering. These reads must remain server-side.

### 7.1 Fetch Neighbor Chunks

Use `document_id` and `ordinal` from an RPC result to fetch surrounding active chunks.

```sql
select
  chunk_id,
  document_id,
  ordinal,
  content,
  heading,
  heading_path,
  url_source
from public.knowledge_chunks
where dataset_namespace = $1
  and embedding_profile = $2
  and is_active is true
  and document_id = $3
  and ordinal between $4 and $5
order by ordinal asc;
```

Example window for a hit at `ordinal = 12`: pass `11` and `13` to retrieve one chunk before and one chunk after.

### 7.2 Fetch a Full Active Document

```sql
select
  chunk_id,
  ordinal,
  content,
  heading,
  heading_path,
  source_line_spans,
  review_required,
  review_reasons,
  data_quality_flags,
  warnings
from public.knowledge_chunks
where dataset_namespace = $1
  and embedding_profile = $2
  and is_active is true
  and document_id = $3
order by ordinal asc;
```

### 7.3 Filter by Quality Signals

`review_required` means the chunk carries a quality-review signal. It does not mean the row is stale, deleted, or unusable.

Only filter out review-required chunks if the product explicitly decides that user-facing answers should hide them:

```sql
select *
from public.knowledge_chunks
where dataset_namespace = $1
  and embedding_profile = $2
  and is_active is true
  and review_required is false;
```

For most internal retrieval and evaluation workflows, keep review-required chunks visible and surface the quality signals to the answer-construction layer.

## 8. Answer Construction Guidance

Use these fields for user-visible answers and citations:

- `content`: the main display and answer context.
- `policy_title`: readable policy name.
- `heading_path`: section hierarchy for citation labels.
- `url_source`: source URL.
- `reference_number`: policy code when available.
- `source_line_spans`: source line ranges for internal traceability or detailed citations.
- `document_id` and `ordinal`: ordering, grouping, and neighbor expansion.

Recommended citation label:

```text
{policy_title} / {heading_path joined with " > "} ({reference_number})
```

Omit `reference_number` when it is null.

Treat these as quality signals:

- `review_required`
- `review_reasons`
- `data_quality_flags`
- `warnings`

They are not deletion flags. Deletion/staleness is represented by `is_active=false`, and retrieval consumers should normally ignore inactive rows by filtering for `is_active is true`.

Do not show raw embeddings to end users. Do not let user-visible answer generation depend on `dataset_run_id`, `last_seen_run_id`, `created_at`, or `updated_at`.

## 9. Empty Results and Failure Behavior

Fail closed when retrieval configuration does not match the database.

Recommended behavior:

- If no active rows exist for `(dataset_namespace, embedding_profile)`, return a configuration error to operators rather than silently querying another profile.
- If the query embedding is not exactly 1536 dimensions, reject the request before calling the database.
- If the RPC returns no rows at the chosen threshold, either retry with a lower threshold or return a no-answer response. Do not switch profiles automatically.
- If `match_threshold` is outside `[-1.0, 1.0]`, fix the caller configuration. The database function will reject it.

Diagnostic query for active rows:

```sql
select count(*) as active_chunks
from public.knowledge_chunks
where dataset_namespace = $1
  and embedding_profile = $2
  and is_active is true;
```

## 10. Compatibility and Evolution

The current retrieval contract is tied to:

```text
schema_version: chunk.v1
embedding dimensions: 1536
embedding profile shape: {provider}:{model}:1536:v1
```

Compatibility rules:

- A different provider, model, dimension count, or embedding input version is a different `embedding_profile`.
- Do not mix query embeddings across profiles.
- Do not merge results from different profiles unless the application explicitly treats them as separate indexes and re-ranks them outside this database contract.
- The current schema only supports `extensions.vector(1536)` until a future migration changes both the table column and RPC signature.
- Additive columns may appear in the table, but existing column semantics in this document should remain stable until a new contract version is published.
- Breaking changes to chunk text, embedding dimensions, vector distance behavior, or RPC return shape require a new retrieval contract.

## 11. Verification Status

| Item | Status | Source |
|---|---|---|
| Table schema | Verified | `supabase/migrations/202606250001_create_knowledge_chunks.sql` |
| RPC signature and behavior | Verified | `public.match_knowledge_chunks(...)` definition in migration |
| Vector dimension constraint | Verified | Migration defines `extensions.vector(1536)` and `embedding_dimensions = 1536` check |
| RLS enablement and grants/revokes | Verified | Migration enables RLS, revokes table access from `anon` and `authenticated`, and grants RPC execution to `service_role` |
| Observed local embedding profile | Verified | `data/ingesting/embedding_cache.jsonl` contains 458 cached embeddings for `google-gemini:gemini-embedding-001:1536:v1` |
| Active PostgreSQL embedding profile | Not independently verified | Requires an active-row profile query against the live database |
| Retrieval-quality threshold | Not evaluated | Requires retrieval evaluation before treating `match_threshold=0.0` or any higher value as product-tuned |
| Python retrieval example | Static documentation example | Included to show expected API usage; not executed as part of this repository's automated tests |

## 12. Minimal Retrieval Checklist

Before serving retrieval traffic, verify:

- The backend holds credentials server-side only.
- The backend identifies the active `embedding_profile` for the target namespace instead of assuming a provider or model.
- The backend generates 1536-dimensional query embeddings with the same provider and model represented by that active profile.
- The backend uses `dataset_namespace='vinuni-policy'` unless another namespace was intentionally ingested.
- The backend uses the exact `embedding_profile` stored in `public.knowledge_chunks`.
- Calls go through `public.match_knowledge_chunks(...)` or direct server-side SQL scoped by namespace, profile, and active status.
- The answer layer cites `content`, `policy_title`, `heading_path`, `url_source`, and `reference_number` where available.
- Quality flags are handled intentionally.
- Empty profile results are treated as configuration failures, not as permission to query another profile.
