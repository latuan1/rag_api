# LibreChat RAG API Contract

This document defines the external RAG service contract consumed by LibreChat through
`RAG_API_URL`. It covers the legacy file-search/vector endpoints already used by file
uploads and tools, and the Local Knowledge-specific endpoints used for private Knowledge
Spaces.

This is not the contract for LibreChat's public `/api/knowledge` user-facing routes.

## Base Contract

### Base URL

LibreChat calls the RAG service at:

```text
${RAG_API_URL}
```

Typical compose and Helm deployments set this to:

```text
http://rag_api:8000
```

All endpoint paths in this document are relative to `RAG_API_URL`.

### Transport and Content Types

- JSON endpoints use `Content-Type: application/json`.
- File upload and extraction endpoints use `multipart/form-data`.
- JSON responses should use `application/json` unless the endpoint explicitly returns plain text.
- The service should use UTF-8 for all text payloads.

### Authentication

Legacy file-search/vector endpoints are called with a short-lived LibreChat JWT:

```http
Authorization: Bearer <short-lived-token>
```

The token identifies the LibreChat user performing file upload, text extraction, query, or
delete operations.

Local Knowledge endpoints are scoped by explicit metadata fields such as `ownerId`,
`tenantId`, `knowledgeSpaceId`, `documentId`, and `fileId`. If the RAG service is deployed
behind an authenticated boundary, it may also require service-to-service authentication, but
LibreChat's current Local Knowledge request shapes rely on scoped payload filters as the
privacy boundary.

### Privacy Boundary

The RAG service must apply user, tenant, Knowledge Space, document, and file constraints
inside its own storage or pgvector query before returning chunks. It must not return broad
matches and rely on LibreChat to filter cross-user or cross-tenant results afterward.

For Local Knowledge, every indexed chunk must preserve:

```text
ownerId
tenantId, when tenant isolation is enabled
knowledgeSpaceId
documentId
fileId
chunkHash
page or section
```

### Idempotency

- Deleting an already-deleted legacy document may return `404`; LibreChat treats this as
  successful in code paths where missing vectors are acceptable.
- Local Knowledge deletion should be idempotent for the same complete filter set.
- Re-indexing the same chunk should upsert or replace by the unique tuple chosen by the RAG
  service, typically `ownerId`, optional `tenantId`, `knowledgeSpaceId`, `documentId`,
  `fileId`, and `chunkHash`.

### Health Check

LibreChat checks RAG availability with `GET /health`. A healthy service returns HTTP `200`.

## Shared Error Model

RAG endpoints should return a consistent JSON error payload:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "RAG retrieval requires ownerId.",
    "details": {
      "field": "ownerId"
    }
  }
}
```

Recommended fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `error.code` | string | Yes | Stable machine-readable error code. |
| `error.message` | string | Yes | Human-readable diagnostic safe for logs. |
| `error.details` | object | No | Structured context such as field name or limit. |

Recommended status codes:

| Status | Meaning |
|---:|---|
| `400` | Invalid request, missing required fields, missing privacy filters, or malformed JSON. |
| `401` | Missing or invalid JWT for legacy authenticated endpoints. |
| `403` | Authenticated caller is not allowed to access the requested scope. |
| `404` | Requested document or context was not found. For supported delete paths, LibreChat may treat this as idempotent success. |
| `409` | Stale, duplicate, or conflicting indexing state that cannot be safely upserted. |
| `413` | Uploaded file exceeds the service size limit. |
| `415` | Unsupported file type. |
| `422` | File parse, extraction, embedding, or indexing failed, such as no extractable text. |
| `429` | Rate limit, concurrency limit, or queue pressure. |
| `500` | Internal RAG service error. |
| `503` | RAG service or backing vector store is unavailable. |

## Legacy File-Search and Vector Endpoints

These endpoints support existing uploaded-file vector search and file-search tools. They are
separate from Local Knowledge.

### GET /health

Checks service readiness.

#### Request

```http
GET /health
```

#### Success Response

```http
HTTP/1.1 200 OK
```

The response body may be empty, plain `OK`, or a small JSON object:

```json
{
  "status": "ok"
}
```

### POST /text

Extracts text from an uploaded file. LibreChat uses this as an optional parser before falling
back to native text parsing.

#### Request

```http
POST /text
Authorization: Bearer <short-lived-token>
Accept: application/json
Content-Type: multipart/form-data
```

Multipart fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `file_id` | string | Yes | LibreChat file id. |
| `file` | file | Yes | Uploaded source file stream. |

#### Success Response

```json
{
  "text": "Extracted file text..."
}
```

LibreChat computes the returned byte length from `text`.

#### Example

```bash
curl -X POST "$RAG_API_URL/text" \
  -H "Authorization: Bearer $LIBRECHAT_JWT" \
  -H "Accept: application/json" \
  -F "file_id=file_abc123" \
  -F "file=@./policy.pdf"
```

### POST /embed

Embeds an uploaded file into the legacy vector store.

#### Request

```http
POST /embed
Authorization: Bearer <short-lived-token>
Accept: application/json
Content-Type: multipart/form-data
```

Multipart fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `file_id` | string | Yes | LibreChat file id. |
| `file` | file | Yes | Uploaded source file stream. |
| `entity_id` | string | No | Optional shared-resource or assistant/agent scope. |
| `storage_metadata` | JSON string | No | Serialized storage metadata for dual-storage retrieval. |

#### Success Response

```json
{
  "status": true,
  "known_type": true
}
```

`known_type: false` means LibreChat treats the upload as unsupported.

#### Example

```bash
curl -X POST "$RAG_API_URL/embed" \
  -H "Authorization: Bearer $LIBRECHAT_JWT" \
  -H "Accept: application/json" \
  -F "file_id=file_abc123" \
  -F "entity_id=agent_123" \
  -F 'storage_metadata={"bucket":"uploads","key":"file_abc123.pdf"}' \
  -F "file=@./policy.pdf"
```

### POST /query

Runs semantic search for a legacy uploaded file.

#### Request

```http
POST /query
Authorization: Bearer <short-lived-token>
Content-Type: application/json
```

```json
{
  "file_id": "file_abc123",
  "query": "What are the retention rules?",
  "k": 5,
  "entity_id": "agent_123"
}
```

Request fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `file_id` | string | Yes | File id to search. |
| `query` | string | Yes | Natural-language search query. |
| `k` | number | Yes | Number of nearest chunks to return. |
| `entity_id` | string | No | Optional shared-resource or assistant/agent scope. |

#### Success Response

The current LibreChat consumers expect an array of result tuples:

```json
[
  [
    {
      "page_content": "Employees must retain policy acknowledgements for seven years.",
      "metadata": {
        "source": "/uploads/file_abc123/policy.pdf",
        "page": 12
      }
    },
    0.18
  ]
]
```

Tuple fields:

| Position | Type | Description |
|---:|---|---|
| `0` | object | Document/chunk info. |
| `0.page_content` | string | Retrieved chunk text. |
| `0.metadata.source` | string | Source path or source identifier. |
| `0.metadata.page` | number | Optional page number. |
| `1` | number | Distance, where lower is more relevant. LibreChat converts relevance with `1.0 - distance`. |

#### Example

```bash
curl -X POST "$RAG_API_URL/query" \
  -H "Authorization: Bearer $LIBRECHAT_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "file_abc123",
    "query": "What are the retention rules?",
    "k": 5,
    "entity_id": "agent_123"
  }'
```

### GET /documents/{file_id}/context

Returns full context for a legacy uploaded file when `RAG_USE_FULL_CONTEXT` is enabled.

#### Request

```http
GET /documents/file_abc123/context
Authorization: Bearer <short-lived-token>
```

#### Success Response

The current caller accepts a text-like response or JSON value that can be interpolated into
the prompt context.

```text
Full extracted document context...
```

### DELETE /documents

Deletes one or more legacy embedded files from the vector store.

#### Request

```http
DELETE /documents
Authorization: Bearer <short-lived-token>
Content-Type: application/json
Accept: application/json
```

```json
["file_abc123", "file_def456"]
```

#### Success Response

```json
{
  "deleted": ["file_abc123", "file_def456"]
}
```

The body is not currently used by LibreChat. HTTP success is sufficient.

#### Example

```bash
curl -X DELETE "$RAG_API_URL/documents" \
  -H "Authorization: Bearer $LIBRECHAT_JWT" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '["file_abc123"]'
```

## Local Knowledge Endpoints

Local Knowledge is private per user and uses explicit metadata filters for indexing,
retrieval, and deletion. The current TypeScript integration lives in
`packages/api/src/knowledge/rag.ts`.

### Shared Local Knowledge Types

#### Document Metadata

```json
{
  "ownerId": "user_123",
  "tenantId": "tenant_a",
  "knowledgeSpaceId": "space_123",
  "documentId": "doc_123",
  "fileId": "file_abc123"
}
```

Fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `ownerId` | string | Yes | LibreChat user id that owns the Knowledge Space and document. |
| `tenantId` | string | No | Tenant id when tenant isolation is enabled. |
| `knowledgeSpaceId` | string | Yes | Knowledge Space id. |
| `documentId` | string | Yes | Knowledge document id. |
| `fileId` | string | Yes | Stored LibreChat file id. |

#### Chunk Metadata

```json
{
  "ownerId": "user_123",
  "tenantId": "tenant_a",
  "knowledgeSpaceId": "space_123",
  "documentId": "doc_123",
  "fileId": "file_abc123",
  "chunkHash": "6c93d0f50...",
  "page": 2,
  "section": "chunk-1"
}
```

Fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `chunkHash` | string | Yes | Stable SHA-256 chunk hash generated by LibreChat. |
| `page` | number | Conditionally | Page number where the chunk originated, when available. |
| `section` | string | Conditionally | Section or fallback chunk label. |

At least one of `page` or `section` must be present.

LibreChat currently chunks Local Knowledge text with:

```text
chunkSizeTokens = 800
chunkOverlapTokens = 120
```

### POST /knowledge/index

Recommended HTTP shape for Local Knowledge chunk indexing.

Current implementation note: LibreChat represents indexing through
`RagIndexDeps.sendChunk(request)` rather than hard-coding an HTTP call. Any RAG service
adapter used for Local Knowledge should implement this exact chunk-level request shape.

#### Request

```http
POST /knowledge/index
Content-Type: application/json
```

```json
{
  "text": "Employees must retain policy acknowledgements for seven years.",
  "metadata": {
    "ownerId": "user_123",
    "tenantId": "tenant_a",
    "knowledgeSpaceId": "space_123",
    "documentId": "doc_123",
    "fileId": "file_abc123",
    "chunkHash": "6c93d0f50d1c4b0f2a3f4e5d6c7b8a9",
    "page": 12,
    "section": "Retention"
  }
}
```

Request fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `text` | string | Yes | Chunk text to embed and store. |
| `metadata` | object | Yes | Chunk metadata described above. |

#### Success Response

```json
{
  "status": "indexed",
  "chunkHash": "6c93d0f50d1c4b0f2a3f4e5d6c7b8a9"
}
```

The response body is adapter-defined today; success or thrown failure is what LibreChat
requires from `sendChunk`.

#### Example

```bash
curl -X POST "$RAG_API_URL/knowledge/index" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Employees must retain policy acknowledgements for seven years.",
    "metadata": {
      "ownerId": "user_123",
      "tenantId": "tenant_a",
      "knowledgeSpaceId": "space_123",
      "documentId": "doc_123",
      "fileId": "file_abc123",
      "chunkHash": "6c93d0f50d1c4b0f2a3f4e5d6c7b8a9",
      "page": 12,
      "section": "Retention"
    }
  }'
```

### POST /knowledge/query

Retrieves Local Knowledge chunks with privacy filters applied inside the RAG service.

#### Request

```http
POST /knowledge/query
Content-Type: application/json
```

```json
{
  "query": "What are the retention rules?",
  "filters": {
    "ownerId": "user_123",
    "tenantId": "tenant_a",
    "knowledgeSpaceIds": ["space_123"],
    "documentIds": ["doc_123"],
    "statuses": ["ready", "ready_with_warnings"]
  },
  "topK": 12,
  "minRelevanceScore": 0.35,
  "maxChunksPerDocument": 4,
  "maxTotalChunks": 12
}
```

Request fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `query` | string | Yes | Natural-language retrieval query. |
| `filters.ownerId` | string | Yes | Current LibreChat user id. |
| `filters.tenantId` | string | No | Current tenant id when enabled. |
| `filters.knowledgeSpaceIds` | string[] | Yes | Allowed Knowledge Space ids. Must not be empty. |
| `filters.documentIds` | string[] | Yes | Eligible document ids. Must not be empty. |
| `filters.statuses` | string[] | Yes | Eligible document statuses. |
| `topK` | number | Yes | Candidate count before final caps. Current default is `12`. |
| `minRelevanceScore` | number | Yes | Minimum relevance score. Current default is `0.35`. |
| `maxChunksPerDocument` | number | Yes | Per-document cap. Current default is `4`. |
| `maxTotalChunks` | number | Yes | Overall returned chunk cap. Current default is `12`. |

Allowed `filters.statuses` values:

```json
["ready", "ready_with_warnings"]
```

The RAG service must not search when `knowledgeSpaceIds` or `documentIds` is empty. LibreChat
short-circuits those empty selections before calling the service, and the service should keep
the same safety behavior.

#### Success Response

```json
[
  {
    "knowledgeSpaceId": "space_123",
    "knowledgeSpaceName": "Company Docs",
    "documentId": "doc_123",
    "fileId": "file_abc123",
    "filename": "policy.pdf",
    "chunkText": "Employees must retain policy acknowledgements for seven years.",
    "chunkHash": "6c93d0f50d1c4b0f2a3f4e5d6c7b8a9",
    "page": 12,
    "section": "Retention",
    "score": 0.87
  }
]
```

Response fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `knowledgeSpaceId` | string | Yes | Source Knowledge Space id. |
| `knowledgeSpaceName` | string | No | Display name for citations and context. |
| `documentId` | string | Yes | Source document id. |
| `fileId` | string | No | Source file id. |
| `filename` | string | No | Source file name. |
| `chunkText` | string | Yes | Retrieved chunk text. |
| `chunkHash` | string | No | Stored chunk hash for dedupe/citation matching. |
| `page` | number | No | Source page. |
| `section` | string | No | Source section or chunk label. |
| `score` | number | Yes | Relevance score, where higher is more relevant. |

#### Example

```bash
curl -X POST "$RAG_API_URL/knowledge/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the retention rules?",
    "filters": {
      "ownerId": "user_123",
      "tenantId": "tenant_a",
      "knowledgeSpaceIds": ["space_123"],
      "documentIds": ["doc_123"],
      "statuses": ["ready", "ready_with_warnings"]
    },
    "topK": 12,
    "minRelevanceScore": 0.35,
    "maxChunksPerDocument": 4,
    "maxTotalChunks": 12
  }'
```

### POST /knowledge/delete

Deletes Local Knowledge embeddings for a single document using complete privacy filters.

#### Request

```http
POST /knowledge/delete
Content-Type: application/json
```

```json
{
  "ownerId": "user_123",
  "tenantId": "tenant_a",
  "knowledgeSpaceId": "space_123",
  "documentId": "doc_123",
  "fileId": "file_abc123"
}
```

Request fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `ownerId` | string | Yes | Owner of the Knowledge document. |
| `tenantId` | string | No | Tenant id when tenant isolation is enabled. |
| `knowledgeSpaceId` | string | Yes | Knowledge Space id. |
| `documentId` | string | Yes | Knowledge document id. |
| `fileId` | string | Yes | Stored LibreChat file id. |

#### Success Response

```json
{
  "deleted": true,
  "documentId": "doc_123",
  "fileId": "file_abc123"
}
```

The response body is not currently used by LibreChat. HTTP success is sufficient.

#### Example

```bash
curl -X POST "$RAG_API_URL/knowledge/delete" \
  -H "Content-Type: application/json" \
  -d '{
    "ownerId": "user_123",
    "tenantId": "tenant_a",
    "knowledgeSpaceId": "space_123",
    "documentId": "doc_123",
    "fileId": "file_abc123"
  }'
```

## Implementation References

Current LibreChat call sites and source-of-truth types:

- Local Knowledge RAG types and calls: `packages/api/src/knowledge/rag.ts`
- Local Knowledge chunk size, overlap, and chunk hashes: `packages/api/src/knowledge/chunks.ts`
- RAG text extraction fallback path: `packages/api/src/files/text.ts`
- Shared RAG file deletion helper: `packages/api/src/files/rag.ts`
- Legacy vector upload/delete implementation: `api/server/services/Files/VectorDB/crud.js`
- Legacy prompt context retrieval: `api/app/clients/prompts/createContextHandlers.js`
- Legacy file-search tool query handling: `api/app/clients/tools/util/fileSearch.js`
