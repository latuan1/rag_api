# Local Knowledge Retrieval API Contract for LibreChat

**Status:** Implemented HTTP API contract for integrating LibreChat with this RAG API's Local Knowledge retrieval layer  
**Contract version:** 1.0  
**Last verified against commit:** `fe2d1c9cb6d793317ea095a34f0914ce23793a89`  
**Last verified date:** 2026-06-28  
**Audience:** LibreChat backend maintainers and trusted server-side integration code  
**Base path:** `/knowledge` relative to the RAG API server root  
**Database contract:** [`docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md`](LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md)

This document describes the implemented FastAPI HTTP surface that LibreChat can call from its backend to retrieve from the Local Knowledge database. It is intentionally API-facing: it documents request and response shapes, authentication behavior, configuration ownership, validation bounds, failure handling, ordering, and compatibility expectations.

Do not call these endpoints directly from browsers, mobile clients, desktop clients, or other untrusted user-controlled code. LibreChat should call this API from its server process only.

## 1. Endpoint URL Construction

LibreChat constructs Local Knowledge endpoint URLs by joining its configured RAG API base URL with the `/knowledge` path:

```text
{RAG_API_URL}/knowledge/...
```

Examples:

```text
http://rag_api:8000/knowledge/profiles
http://rag_api:8000/knowledge/retrieve
http://rag_api:8000/knowledge/documents/{document_id}
http://rag_api:8000/knowledge/documents/{document_id}/neighbors
```

`RAG_API_URL` is a LibreChat-side client setting. `/knowledge` is relative to the `rag_api` server root.

## 2. Security Boundary

`/knowledge/*` is a trusted-backend-only API.

LibreChat must not expose any of the following to end users or client-side code:

- `KNOWLEDGE_API_TOKEN`
- `RETRIEVAL_DATABASE_URL`
- PostgreSQL credentials
- Supabase service credentials
- embedding provider API keys
- raw embedding vectors
- raw database error details
- the `/knowledge/*` endpoints as unauthenticated public routes

LibreChat's browser-facing UI should call LibreChat's own backend. The LibreChat backend can then call this RAG API with server-held credentials.

## 3. Configuration Ownership

LibreChat should own only the client-side integration settings needed to call this HTTP API:

| LibreChat-owned setting | Required | Description |
|---|---:|---|
| `RAG_API_URL` | yes | Base URL of the RAG API service, for example `http://rag_api:8000`. |
| `KNOWLEDGE_API_TOKEN` | yes when using the recommended auth path | Shared backend token sent as `X-Knowledge-API-Key`. Keep this server-side. |
| Client timeout and retry settings | optional | LibreChat-owned HTTP client behavior. This contract does not prescribe exact values. |

All `RETRIEVAL_*` settings belong to the `rag_api` deployment. LibreChat must not duplicate them, expose them, or require them in its own browser-facing configuration.

The implemented `rag_api` retrieval service is configured with:

| `rag_api`-owned variable | Default | Description |
|---|---|---|
| `RETRIEVAL_DATABASE_URL` | existing app `DSN` | PostgreSQL DSN for the Supabase/pgvector knowledge database. Do not set this to `SUPABASE_URL`, `SUPABASE_ANON_KEY`, or `SUPABASE_SERVICE_ROLE_KEY`. |
| `RETRIEVAL_DATASET_NAMESPACE` | `vinuni-policy` | Dataset namespace used to scope profile discovery and retrieval. |
| `RETRIEVAL_EMBEDDING_API_KEY` | unset | API key for the OpenAI-compatible embeddings endpoint used at query time. |
| `RETRIEVAL_EMBEDDING_BASE_URL` | unset | OpenAI-compatible embeddings base URL. Required for the default `google-gemini` provider profile. |
| `RETRIEVAL_EMBEDDING_PROVIDER` | `google-gemini` | Stored provider slug expected in `public.knowledge_chunks`; this does not select a generic provider client. |
| `RETRIEVAL_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model expected in the active database profile and sent to the OpenAI-compatible embeddings API. |
| `RETRIEVAL_EMBEDDING_DIMENSIONS` | `1536` | Query embedding size. The implemented service requires exactly `1536`. |
| `RETRIEVAL_EMBEDDING_INPUT_VERSION` | `v1` | Query input contract version. The implemented service supports only `v1`. |
| `RETRIEVAL_EMBEDDING_PROFILE` | unset | Exact active profile string, for example `google-gemini:gemini-embedding-001:1536:v1`. Omit only when the namespace has exactly one active profile. |
| `RETRIEVAL_DEFAULT_MATCH_COUNT` | `10` | Default result count when `POST /knowledge/retrieve` omits `match_count`. Must be `1..50`. |
| `RETRIEVAL_DEFAULT_MATCH_THRESHOLD` | `0.0` | Default similarity threshold when `POST /knowledge/retrieve` omits `match_threshold`. Must be `-1.0..1.0`. |
| `RETRIEVAL_DATABASE_COMMAND_TIMEOUT` | `30` | asyncpg command timeout in seconds. Must be greater than `0`. |
| `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE` | `100` | asyncpg prepared statement cache size. Set to `0` for Supavisor transaction mode on port `6543`. |
| `RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS` | `30` | Embedding API timeout in seconds. Must be greater than `0`. |

## 4. Authentication

LibreChat should use the shared backend token path for server-to-server calls:

```http
X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>
```

The implemented API also accepts a request that has already passed the existing `rag_api` JWT middleware and has populated `request.state.user`. LibreChat must not assume its own end-user session JWT is accepted by `rag_api`. JWT authentication is valid only when the caller has a token that matches the `rag_api` JWT secret and middleware contract.

If both auth paths are configured, either one can authenticate the request.

Authentication failures are intentionally stable:

| Condition | Status | Body |
|---|---:|---|
| Neither `KNOWLEDGE_API_TOKEN` nor JWT auth is configured in `rag_api` | `503` | `{"detail":"Local knowledge retrieval authentication is not configured"}` |
| Auth is configured, but credentials are missing or invalid | `401` | `{"detail":"Unauthorized"}` |

## 5. Profile and Embedding Rules

Retrieval fails closed when the configured `rag_api` profile does not match the active database profile.

The implemented service enforces these rules:

- `RETRIEVAL_EMBEDDING_DIMENSIONS` must be exactly `1536`.
- `RETRIEVAL_EMBEDDING_INPUT_VERSION` must be exactly `v1`.
- Query embeddings must contain exactly `1536` finite numeric values.
- Non-OpenAI provider slugs require `RETRIEVAL_EMBEDDING_BASE_URL`.
- `gemini-embedding-001` requires an explicit OpenAI-compatible base URL.
- If `RETRIEVAL_EMBEDDING_PROFILE` is set, exactly one active database profile must match it.
- If `RETRIEVAL_EMBEDDING_PROFILE` is unset, exactly one active profile must exist for the configured namespace.
- The resolved active profile's provider, model, dimensions, and input version must match the configured values.
- The service does not automatically switch profiles or merge results across profiles.

The active profile shape is:

```text
{embedding_provider}:{embedding_model}:1536:v1
```

Example:

```text
google-gemini:gemini-embedding-001:1536:v1
```

The current retrieval transport is OpenAI-compatible embeddings. For `RETRIEVAL_EMBEDDING_INPUT_VERSION=v1`, the service sends the raw user query as:

```json
{
  "input": ["<query>"]
}
```

The service never returns raw embeddings in HTTP responses.

## 6. Endpoint: `GET /knowledge/profiles`

Discovers active embedding profiles for the configured `RETRIEVAL_DATASET_NAMESPACE`.

This endpoint does not call the embedding provider. It only queries the knowledge database. LibreChat may call it for diagnostics or readiness checks. Failure of this optional check should not prevent LibreChat from starting unless the deployment explicitly treats Local Knowledge as a required hard dependency.

### Request

```http
GET /knowledge/profiles
X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>
```

### Response `200`

```json
{
  "dataset_namespace": "vinuni-policy",
  "profiles": [
    {
      "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
      "embedding_provider": "google-gemini",
      "embedding_model": "gemini-embedding-001",
      "embedding_dimensions": 1536,
      "embedding_input_version": "v1",
      "active_chunks": 458
    }
  ]
}
```

### Response Fields

All fields in this response are non-null according to the implemented HTTP response model.

| Field | Type | Description |
|---|---|---|
| `dataset_namespace` | string | Configured namespace queried by the service. |
| `profiles` | array | Active profile groups in the namespace. May be empty. |
| `profiles[].embedding_profile` | string | Exact profile string used by retrieval. |
| `profiles[].embedding_provider` | string | Stored provider slug. |
| `profiles[].embedding_model` | string | Stored embedding model. |
| `profiles[].embedding_dimensions` | integer | Stored embedding dimensions; currently `1536`. |
| `profiles[].embedding_input_version` | string | Stored input version; currently `v1`. |
| `profiles[].active_chunks` | integer | Number of active chunks in this profile group. |

### Ordering

Profiles are ordered by `active_chunks DESC`, then `embedding_profile ASC`.

## 7. Endpoint: `POST /knowledge/retrieve`

Embeds a query, resolves the active profile, calls the database retrieval RPC, and returns matching chunks.

### Request

```http
POST /knowledge/retrieve
Content-Type: application/json
X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>
```

```json
{
  "query": "What is the grade appeal procedure?",
  "match_count": 10,
  "match_threshold": 0.0,
  "filter_document_type": null
}
```

### Request Fields

| Field | Type | Required | Validation | Description |
|---|---|---:|---|---|
| `query` | string | yes | trimmed, minimum length `1` | User query to embed. |
| `match_count` | integer or null | no | `1..50` | Number of matches. If omitted or null, service uses `RETRIEVAL_DEFAULT_MATCH_COUNT`. |
| `match_threshold` | number or null | no | `-1.0..1.0` | Minimum similarity. If omitted or null, service uses `RETRIEVAL_DEFAULT_MATCH_THRESHOLD`. |
| `filter_document_type` | string or null | no | trimmed, blank normalizes to null | Optional exact `document_type` filter passed to the database RPC. |

Whitespace-only `query` values are rejected with FastAPI/Pydantic validation errors.

### Response `200`

```json
{
  "dataset_namespace": "vinuni-policy",
  "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
  "results": [
    {
      "chunk_id": "chunk-001",
      "document_id": "policy-001",
      "section_id": "article-1",
      "ordinal": 0,
      "content": "Policy text...",
      "embedding_content": "Embedded text...",
      "policy_title": "Academic Regulations",
      "reference_number": "REF-001",
      "document_type": "Policy",
      "heading": "Article 1. Scope",
      "heading_path": ["Academic Regulations", "Article 1. Scope"],
      "url_source": "https://example.edu/policy",
      "similarity": 0.82
    }
  ]
}
```

If no semantic matches are returned by the database RPC, the API returns:

```json
{
  "dataset_namespace": "vinuni-policy",
  "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
  "results": []
}
```

### Response Fields

Only `reference_number` and `document_type` are nullable in the implemented retrieval response model. All other listed fields are non-null HTTP response fields. A database row that supplies null for a non-null HTTP field is a server-side contract violation, not a nullable LibreChat client field.

| Field | Type | Description |
|---|---|---|
| `dataset_namespace` | string | Namespace used for retrieval. |
| `embedding_profile` | string | Exact resolved profile used for retrieval. |
| `results` | array | Matching chunks returned by `public.match_knowledge_chunks(...)`. May be empty. |
| `results[].chunk_id` | string | Matching chunk ID. |
| `results[].document_id` | string | Source document ID. |
| `results[].section_id` | string | Source section ID. |
| `results[].ordinal` | integer | Chunk order within the source document. |
| `results[].content` | string | Display and answer context. LibreChat should use this field for model context and user-visible source rendering. |
| `results[].embedding_content` | string | Diagnostic-only embedded text. LibreChat must not use this as answer context or display it to users. This field may be removed in a future major API version. |
| `results[].policy_title` | string | Source policy title. |
| `results[].reference_number` | string or null | Policy reference number when available. |
| `results[].document_type` | string or null | Document type when available. |
| `results[].heading` | string | Primary heading for the chunk. |
| `results[].heading_path` | string array | Heading hierarchy. |
| `results[].url_source` | string | Source URL. |
| `results[].similarity` | number | Similarity returned by the database RPC. |

Retrieval responses do not include raw vectors.

### Ordering

Retrieval result ordering is produced by `public.match_knowledge_chunks(...)`. The current database contract defines similarity-based ordering for semantic retrieval; LibreChat should preserve the returned order unless it intentionally applies its own reranking.

## 8. Endpoint: `GET /knowledge/documents/{document_id}`

Fetches active chunks for one document, scoped by the configured namespace and resolved active embedding profile.

This endpoint is useful when LibreChat wants to inspect or cite more of the source document after an initial retrieval result.

### Request

```http
GET /knowledge/documents/policy-001?limit=500
X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>
```

### Path and Query Parameters

| Parameter | Location | Type | Validation | Default | Description |
|---|---|---|---|---|---|
| `document_id` | path | string | minimum length `1`, maximum length `255`, not blank after route-level validation | none | Source document ID. |
| `limit` | query | integer | `1..2000` | `500` | Maximum chunks to return. |

### Response `200`

```json
{
  "dataset_namespace": "vinuni-policy",
  "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
  "document_id": "policy-001",
  "chunks": [
    {
      "chunk_id": "chunk-001",
      "ordinal": 0,
      "content": "Policy text...",
      "heading": "Article 1. Scope",
      "heading_path": ["Academic Regulations", "Article 1. Scope"],
      "source_line_spans": [],
      "review_required": false,
      "review_reasons": [],
      "data_quality_flags": [],
      "warnings": []
    }
  ]
}
```

Unknown `document_id` values return `200` with `chunks: []`.

### Response Fields

All listed document chunk fields are non-null in the implemented HTTP response model. A database row that supplies null for one of these fields is a server-side contract violation, not a nullable LibreChat client field.

| Field | Type | Description |
|---|---|---|
| `dataset_namespace` | string | Namespace used for the read. |
| `embedding_profile` | string | Exact resolved profile used for the read. |
| `document_id` | string | Requested document ID. |
| `chunks` | array | Active document chunks ordered by `ordinal`. May be empty. |
| `chunks[].chunk_id` | string | Chunk ID. |
| `chunks[].ordinal` | integer | Chunk order within the document. |
| `chunks[].content` | string | Display and answer context. |
| `chunks[].heading` | string | Primary heading. |
| `chunks[].heading_path` | string array | Heading hierarchy. |
| `chunks[].source_line_spans` | object array | Source line provenance. |
| `chunks[].review_required` | boolean | Quality-review signal. This is not a deletion flag. |
| `chunks[].review_reasons` | string array | Machine-readable review reasons. |
| `chunks[].data_quality_flags` | string array | Source quality flags. |
| `chunks[].warnings` | string array | Ingestion or chunking warnings. |

### Ordering

Document chunks are ordered by `ordinal ASC`.

## 9. Endpoint: `GET /knowledge/documents/{document_id}/neighbors`

Fetches active chunks around a document ordinal, inclusive of the center ordinal.

### Request

```http
GET /knowledge/documents/policy-001/neighbors?ordinal=12&window=1
X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>
```

### Path and Query Parameters

| Parameter | Location | Type | Validation | Default | Description |
|---|---|---|---|---|---|
| `document_id` | path | string | minimum length `1`, maximum length `255`, not blank after route-level validation | none | Source document ID. |
| `ordinal` | query | integer | `>=0` | none | Center chunk ordinal. |
| `window` | query | integer | `0..10` | `1` | Number of chunks before and after `ordinal` to request. |

For `ordinal=12` and `window=1`, the service queries ordinals `11..13`. When `ordinal - window` is negative, the lower bound is clamped to `0`.

### Response `200`

The response envelope and chunk shape are the same as `GET /knowledge/documents/{document_id}`.

```json
{
  "dataset_namespace": "vinuni-policy",
  "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
  "document_id": "policy-001",
  "chunks": [
    {
      "chunk_id": "chunk-011",
      "ordinal": 11,
      "content": "Previous chunk...",
      "heading": "Article 3",
      "heading_path": ["Academic Regulations", "Article 3"],
      "source_line_spans": [],
      "review_required": false,
      "review_reasons": [],
      "data_quality_flags": [],
      "warnings": []
    }
  ]
}
```

Unknown `document_id` values, unknown ordinals, or empty neighbor ranges return `200` with `chunks: []`.

### Ordering

Neighbor chunks are ordered by `ordinal ASC`.

## 10. Error Mapping

The route layer logs internal exception details server-side and returns stable public errors.

| Error class or condition | HTTP status | Public body |
|---|---:|---|
| Authentication not configured | `503` | `{"detail":"Local knowledge retrieval authentication is not configured"}` |
| Missing or invalid configured auth credentials | `401` | `{"detail":"Unauthorized"}` |
| Retrieval configuration error | `503` | `{"detail":"Local knowledge retrieval is unavailable"}` |
| Retrieval database error | `503` | `{"detail":"Local knowledge retrieval is unavailable"}` |
| Retrieval embedding provider error | `502` | `{"detail":"Local knowledge retrieval is unavailable"}` |
| Pydantic/FastAPI request validation error | `422` | Standard FastAPI validation body |

The public error response must not expose connection strings, SQL, provider endpoints, API keys, raw embeddings, or internal exception messages.

## 11. Timeout and Retry Boundary

This API contract does not prescribe LibreChat's client timeout or retry policy.

LibreChat should treat these as non-retryable client/configuration outcomes:

- `401 Unauthorized`
- `422 Unprocessable Entity`

Retries for `502 Bad Gateway` or `503 Service Unavailable` are a consumer-side decision and must be bounded. LibreChat should avoid unbounded retries that can amplify provider, database, or deployment outages.

## 12. Curl Examples

Profile discovery:

```bash
curl "$RAG_API_URL/knowledge/profiles" \
  -H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN"
```

Semantic retrieval:

```bash
curl "$RAG_API_URL/knowledge/retrieve" \
  -H "Content-Type: application/json" \
  -H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN" \
  -d '{
    "query": "What is the grade appeal procedure?",
    "match_count": 10,
    "match_threshold": 0.0,
    "filter_document_type": null
  }'
```

Fetch document chunks:

```bash
curl "$RAG_API_URL/knowledge/documents/policy-001?limit=500" \
  -H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN"
```

Fetch neighboring chunks:

```bash
curl "$RAG_API_URL/knowledge/documents/policy-001/neighbors?ordinal=12&window=1" \
  -H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN"
```

## 13. LibreChat Integration Guidance

Recommended LibreChat backend flow:

1. Keep `RAG_API_URL` and `KNOWLEDGE_API_TOKEN` server-side.
2. Do not duplicate or expose `rag_api` `RETRIEVAL_*` settings in LibreChat.
3. Optionally call `GET /knowledge/profiles` for diagnostics or readiness checks. Do not fail LibreChat startup on this optional check unless the deployment explicitly treats Local Knowledge as a hard dependency.
4. For user questions that should use Local Knowledge, call `POST /knowledge/retrieve` from the LibreChat backend.
5. Use `content`, `policy_title`, `heading_path`, `reference_number`, `url_source`, `document_id`, and `ordinal` to construct answer context and citations.
6. Optionally call `GET /knowledge/documents/{document_id}/neighbors` to expand context around high-confidence hits.
7. Optionally call `GET /knowledge/documents/{document_id}` for source inspection or document-level views.
8. Treat `review_required`, `review_reasons`, `data_quality_flags`, and `warnings` as quality signals, not deletion flags.
9. Do not expose raw retrieval errors or internal service details to the LibreChat browser client.

Example citation label for the current VinUni policy dataset:

```text
{policy_title} / {heading_path joined with " > "} ({reference_number})
```

Omit `reference_number` when it is null. LibreChat retains control over final citation and user-interface formatting.

## 14. Database Contract Boundary

This HTTP API wraps a Supabase PostgreSQL database with pgvector. The authoritative database-level contract is:

```text
docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md
```

LibreChat should not need direct database access when using this HTTP API. If a future integration bypasses the HTTP layer and calls the database directly, it must follow the database contract, including:

- `public.knowledge_chunks`
- `public.match_knowledge_chunks(...)`
- `dataset_namespace`
- `embedding_profile`
- `is_active is true`
- `extensions.vector(1536)`

FastAPI startup does not create, drop, or alter the production knowledge schema. Database object changes are managed separately through reviewed migrations.

## 15. Compatibility Notes

The implemented API contract is tied to:

```text
embedding dimensions: 1536
embedding input version: v1
profile shape: {provider}:{model}:1536:v1
retrieval transport: OpenAI-compatible embeddings API
```

Compatible changes should be additive, such as adding optional fields to responses or adding new endpoints. Breaking changes include changing the profile resolution rules, changing vector dimensions, changing the retrieval response shape, exposing raw embeddings, changing authentication semantics, or silently falling back to another embedding profile.

## 16. Contract Verification

This document was verified against:

- `app/routes/local_knowledge_routes.py`
- `app/services/local_knowledge_retrieval.py`
- `app/models.py`
- `app/config.py`
- `app/middleware.py`
- `main.py`
- `tests/test_local_knowledge_routes.py`
- `tests/services/test_local_knowledge_retrieval.py`
- `docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md`

Command executed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_knowledge_routes.py tests/services/test_local_knowledge_retrieval.py -v
```

