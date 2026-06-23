# Personal Knowledge API Contract

## 1. Purpose

This document is the authoritative integration contract between LibreChat and
`rag_api` for the uploaded-file-only Personal Knowledge MVP.

It documents:

- the existing RAG operations that Personal Knowledge reuses;
- the target behavior required for uploaded-file Personal Knowledge retrieval;
- compatibility requirements for existing LibreChat File Search callers; and
- known differences between the current `rag_api` implementation and the MVP
  target.

The contract is based on the `latuan1/rag_api` `main` revision inspected on
2026-06-12, specifically `app/models.py`, `app/middleware.py`, and
`app/routes/document_routes.py`, together with the current LibreChat RAG
callers.

Connector-backed sources are not part of this MVP. This contract does not
define public behavior for external connectors or connector-derived
provenance.

## 2. Ownership Boundaries

### 2.1 LibreChat owns

LibreChat is authoritative for:

- authenticated user and tenant context;
- uploaded-file Personal Knowledge source membership and state;
- source enabled, disabled, and removed states;
- selection of active uploaded File IDs for a request;
- persisted Agent eligibility and File Search capability checks;
- conversion of retrieval results into File Search artifacts and citations; and
- preventing inactive or unauthorized File IDs from reaching `rag_api`.

LibreChat must resolve the complete uploaded-file Personal Knowledge File-ID
set before calling the batch retrieval operation.

### 2.2 `rag_api` owns

`rag_api` is authoritative for:

- text extraction and document loading;
- chunking;
- embedding creation;
- vector persistence;
- similarity search and distance ordering;
- retrieval-location metadata stored with chunks; and
- physical deletion of indexed vectors.

`rag_api` does not decide whether a Personal Knowledge source, Agent, or
conversation is active. It validates supplied File IDs against the authenticated
retrieval scope as defense in depth.

## 3. Transport and Authentication

### 3.1 Base URL and content types

LibreChat calls the service using `RAG_API_URL`.

- JSON operations use `Content-Type: application/json`.
- File ingestion uses `multipart/form-data`.
- Successful responses use JSON.

### 3.2 Bearer token

Except for `/health`, LibreChat sends a short-lived JWT:

```http
Authorization: Bearer <short-lived-jwt>
```

When `JWT_SECRET` is configured, `rag_api`:

1. requires a Bearer token;
2. verifies it with `HS256`;
3. rejects invalid or expired tokens with `401`; and
4. stores the decoded payload in `request.state.user`.

The authenticated LibreChat user ID is read from the JWT `id` claim.

When `JWT_SECRET` is not configured, the current service permits unauthenticated
requests and uses the retrieval principal `public` unless `entity_id` is
provided. Personal Knowledge deployments must configure the same JWT secret in
LibreChat and `rag_api`; public mode is not an acceptable production
configuration for Personal Knowledge.

Authentication errors retain the current response shape:

```json
{
  "detail": "Missing or invalid Authorization header"
}
```

### 3.3 Retrieval principal and `entity_id`

The default retrieval principal is the authenticated JWT `id`.

`entity_id` is optional and represents an entity scope already authorized by
LibreChat, such as an Agent-owned file scope. It must never be accepted from an
untrusted client without LibreChat first checking that the authenticated user
may use that entity.

For retrieval, a document is in scope when its stored `metadata.user_id`
matches either:

- the authenticated JWT `id`; or
- the validated `entity_id`, when supplied.

Authorization filtering must happen before vector ranking and top-k selection.

## 4. Compatibility Matrix

| Operation | Current LibreChat use | Personal Knowledge MVP status | Compatibility rule |
|---|---|---|---|
| `POST /embed` | Upload and index a File | Existing uploaded File Search records must already be embedded before they can be added to Personal Knowledge | Existing multipart fields and response remain compatible |
| `POST /query` | One request per normal File Search file | Not used for Personal Knowledge batch retrieval | Existing request, response, and empty-result behavior remain unchanged |
| `POST /query_multiple` | Present in `rag_api`, not used by current LibreChat File Search | Canonical Personal Knowledge batch operation | Harden in place without adding a second batch route |
| `GET /documents/{id}/context` | Load indexed file context | Available for existing workflows | Existing path, response, and `404` behavior remain unchanged |
| `DELETE /documents` | Delete vectors for File IDs | Available to existing cleanup flows | Existing body and idempotent LibreChat handling remain unchanged |

Adding an uploaded file to Personal Knowledge does not call `/embed`, copy the
file, or create new vectors. It creates LibreChat-owned source membership around
an existing embedded File record.

## 5. Shared Document Tuple

Existing query operations serialize each `(Document, score)` pair as a
two-element JSON array:

```json
[
  {
    "page_content": "The support handbook requires approval before publishing.",
    "metadata": {
      "file_id": "file-a",
      "user_id": "user-123",
      "source": "support-handbook.md",
      "page": 2
    }
  },
  0.12
]
```

The first element is the LangChain document representation. The second element
is the vector-store score.

For the default pgvector implementation, the score is a distance:

- lower values are more relevant;
- results are ordered by ascending distance; and
- `RAG_DISTANCE_THRESHOLD`, when configured, removes results above the
  threshold.

LibreChat must not assume those score semantics for a different vector-store
backend without an explicit adapter. The current Atlas implementation exposes
similarity scores with the opposite direction and does not apply
`RAG_DISTANCE_THRESHOLD`.

## 6. `POST /embed`

### 6.1 Purpose

Extract, chunk, embed, and store one LibreChat File under its `file_id`.

Personal Knowledge does not introduce a new ingestion route. A File Search
upload must already have completed embedding before LibreChat can add it to
Personal Knowledge.

### 6.2 Request

```http
POST /embed
Authorization: Bearer <short-lived-jwt>
Content-Type: multipart/form-data
```

Canonical multipart fields:

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `file_id` | string | yes | LibreChat File ID used for all stored chunks |
| `file` | binary | yes | Source file content |
| `entity_id` | string | no | Validated entity retrieval scope |

Current LibreChat request construction:

```text
file_id=<LibreChat File.file_id>
file=<binary stream>
entity_id=<authorized entity ID, when applicable>
```

LibreChat must not send or rely on an undeclared `storage_metadata` multipart
field. The compatible ingestion contract remains `file_id`, `file`, and
optional `entity_id`.

### 6.3 Stored metadata

For every chunk, the current implementation adds:

```json
{
  "file_id": "file-a",
  "user_id": "user-123",
  "digest": "<md5-of-chunk-content>"
}
```

Loader-provided metadata such as `source` and `page` is retained.

For the MVP:

- `file_id` and `user_id` remain required retrieval metadata;
- page, line, and section fields are retrieval-location metadata when present;
- LibreChat-owned File records remain authoritative for filename and uploaded
  file source membership; and
- LibreChat enriches citations from its authorized File records instead of
  trusting `rag_api` to determine source lifecycle state.

For supported plain-text and Markdown inputs, `rag_api` must derive stable
`start_line`, `end_line`, and optional `section` metadata during document
loading and chunking. Existing loader-provided page metadata remains unchanged.
This is an implementation hardening of `/embed`, not a new multipart field.

### 6.4 Success response

```json
{
  "status": true,
  "message": "File processed successfully.",
  "file_id": "file-a",
  "filename": "support-handbook.md",
  "known_type": true
}
```

`known_type` is loader-dependent. Current LibreChat treats `known_type: false`
or `status: false` as embedding failure.

### 6.5 Errors

| Status | Condition |
|---:|---|
| `400` | Invalid path, unsupported or unreadable content, loader failure, or processing error |
| `401` | Missing, invalid, or expired JWT when authentication is enabled |
| `422` | Missing required multipart fields or invalid field types |
| `500` | Vector persistence failure represented as an HTTP failure |

## 7. `POST /query`

### 7.1 Purpose

Retrieve chunks for one File ID. This remains the normal File Search contract.

### 7.2 Request

```json
{
  "query": "What approval is required before publishing?",
  "file_id": "file-a",
  "k": 5,
  "entity_id": "agent-456"
}
```

| Field | Type | Required | Current default |
|---|---|---:|---:|
| `query` | string | yes | none |
| `file_id` | string | yes | none |
| `k` | integer | no | `4` |
| `entity_id` | string | no | authenticated JWT `id` scope |

### 7.3 Success response

The response is a JSON array of document tuples:

```json
[
  [
    {
      "page_content": "Publishing requires manager approval.",
      "metadata": {
        "file_id": "file-a",
        "user_id": "user-123",
        "source": "support-handbook.md",
        "page": 2
      }
    },
    0.18
  ]
]
```

The current implementation returns `200 []` when no chunks match or when the
first matching document is outside the accepted user/entity scope.

### 7.4 Compatibility requirement

Personal Knowledge changes must not alter:

- the route path;
- request field names;
- default `k`;
- tuple response shape;
- current empty-array behavior; or
- existing normal File Search callers that issue one request per file.

## 8. `POST /query_multiple`

### 8.1 Purpose

This is the canonical Personal Knowledge batch retrieval operation.

It performs one embedding operation and one globally ranked vector search over
the authorized subset of all supplied File IDs. It must not loop over File IDs,
reserve results per file, or merge independently sampled per-file result sets.

### 8.2 Target request

```json
{
  "query": "What is required before publishing the handbook?",
  "file_ids": ["file-a", "file-b"],
  "k": 10,
  "entity_id": "agent-456"
}
```

| Field | Type | Required | Target constraint |
|---|---|---:|---|
| `query` | string | yes | Trimmed, non-empty |
| `file_ids` | string array | yes | 1-50 non-empty values; duplicates removed while preserving first occurrence |
| `k` | integer | no | Default `4`; minimum `1`, maximum `50` |
| `entity_id` | string | no | Trimmed, non-empty validated entity scope |

The default remains `4` to preserve the existing `QueryMultipleBody` behavior.
LibreChat Personal Knowledge callers should explicitly send `k: 10`.

LibreChat resolves active uploaded-file sources before this call and sends no
more than 50 File IDs for the MVP.

### 8.3 Authorization

The service must:

1. derive the authenticated user from the JWT `id`;
2. construct the allowed ownership scope from the user ID and validated
   optional `entity_id`;
3. filter documents by both `file_id` and `metadata.user_id` before vector
   ordering and top-k selection; and
4. never return a chunk owned by another scope.

Unknown and out-of-scope File IDs are omitted from retrieval. A request
containing both authorized and unauthorized IDs searches only the authorized
subset and returns `200`. When no supplied ID is in scope, the endpoint returns
`200 []`. This avoids disclosing whether another user's File ID exists.

LibreChat remains responsible for treating an unexpected omission as a
security or consistency signal in logs without exposing File IDs in metrics.

### 8.4 Retrieval behavior

The operation must:

- call the query embedding provider exactly once;
- issue one vector search using an `IN`/`$in` File-ID filter;
- include ownership scope in the database/vector filter before ranking;
- order the complete authorized result set globally;
- apply the configured distance threshold consistently with `/query`; and
- return no more than `k` tuples.

Example ranking:

| File | Distance |
|---|---:|
| `file-a` | `0.40` |
| `file-a` | `0.50` |
| `file-b` | `0.10` |
| `file-b` | `0.20` |

With `k: 3`, the returned File-ID order is:

```text
file-b, file-b, file-a
```

### 8.5 Success response

The endpoint preserves the existing tuple array and introduces no response
envelope:

```json
[
  [
    {
      "page_content": "Publishing requires manager approval before release.",
      "metadata": {
        "file_id": "file-b",
        "user_id": "user-123",
        "source": "support-handbook.md",
        "start_line": 42,
        "end_line": 58,
        "section": "Publishing checklist"
      }
    },
    0.12
  ],
  [
    {
      "page_content": "The quarterly policy PDF lists the same approval step.",
      "metadata": {
        "file_id": "file-a",
        "user_id": "user-123",
        "source": "policy.pdf",
        "page": 7
      }
    },
    0.19
  ]
]
```

An empty match is successful:

```http
HTTP/1.1 200 OK
```

```json
[]
```

LibreChat must not create Personal Knowledge sources, anchors, or citations
when this response is empty.

### 8.6 Validation and errors

| Status | Condition |
|---:|---|
| `200` | Successful retrieval, including no authorized matches |
| `401` | Missing, invalid, or expired JWT when authentication is enabled |
| `422` | Empty query, empty File-ID array, more than 50 IDs, invalid `k`, or malformed optional fields |
| `500` | Embedding or vector-store failure |

Authorization does not use `404`, because File-ID existence must not be
disclosed. It does not use `403` for mixed or all-omitted File-ID sets; those
sets produce an authorized empty or partial result.

### 8.7 Current implementation status

The current `rag_api` implementation satisfies the Personal Knowledge
`/query_multiple` hardening described above while preserving the existing route
and tuple-array response shape. It provides:

- `POST /query_multiple`;
- `QueryMultipleBody.entity_id`;
- trimmed, non-empty `query` validation;
- trimmed, deduplicated, non-empty `file_ids` validation with a 50 unique
  File-ID maximum;
- `k` bounded to `1..50` with the existing default of `4`;
- one cached query embedding;
- one vector search using a `$in` File-ID filter;
- ownership filtering by `metadata.user_id` before vector ranking and top-k
  selection;
- global top-`k` ranking;
- configured pgvector distance-threshold behavior consistent with `/query`;
- `200 []` for empty authorized batch results; and
- the existing document tuple response shape.

Supported plain-text and Markdown ingestion also derives `start_line`,
`end_line`, and optional Markdown `section` metadata for stored chunks when
source mapping succeeds.

## 9. `GET /documents/{id}/context`

### 9.1 Purpose

Load and combine the indexed chunks for one File ID.

### 9.2 Request

```http
GET /documents/file-a/context
Authorization: Bearer <short-lived-jwt>
```

### 9.3 Success response

The response is the result of the existing `process_documents` operation. Its
shape remains unchanged by Personal Knowledge.

### 9.4 Errors

| Status | Condition |
|---:|---|
| `401` | Missing, invalid, or expired JWT when authentication is enabled |
| `404` | File ID does not exist or contains no indexed documents |
| `400` | Document loading or processing error |

The current route does not perform the Personal Knowledge source authorization
decision. LibreChat must authorize access before calling it.

## 10. `DELETE /documents`

### 10.1 Purpose

Physically delete all indexed chunks for one or more File IDs.

### 10.2 Request

```http
DELETE /documents
Authorization: Bearer <short-lived-jwt>
Content-Type: application/json
```

```json
["file-a", "file-b"]
```

The body is a JSON array of File IDs, not an object.

### 10.3 Success response

```json
{
  "message": "Documents for 2 files deleted successfully"
}
```

### 10.4 Errors and caller behavior

| Status | Condition |
|---:|---|
| `401` | Missing, invalid, or expired JWT when authentication is enabled |
| `404` | One or more requested IDs were not found |
| `422` | Body is not a JSON string array |
| `500` | Vector-store deletion failure |

Current LibreChat deletion callers treat `404` as an idempotent success because
the vectors are already absent. Personal Knowledge cleanup must preserve that
behavior.

Disabling or removing a Personal Knowledge source must stop retrieval
immediately in LibreChat. Physical deletion may happen later only when normal
File deletion rules allow it.

## 11. Metadata and Citation Contract

### 11.1 Trusted sources

Metadata has two trust levels:

| Data | Authority |
|---|---|
| `file_id`, chunk content, page, line, section, vector score | `rag_api` retrieval result |
| filename, uploaded-file source kind, Personal Knowledge source ID | Authorized LibreChat File/source records |

LibreChat must join each returned `metadata.file_id` to the File records it
authorized for the request. A result whose File ID is not in that authorized
map must be discarded.

### 11.2 Uploaded-file citation data

Uploaded-file citations use:

- LibreChat File ID and filename;
- explicit source kind `uploaded_file` added by LibreChat;
- optional Personal Knowledge source ID from LibreChat source membership;
- returned chunk content and score; and
- returned page, line, section, or other location metadata when available.

Retrieval metadata may help locate a chunk, but it must not override
LibreChat-owned File ownership, source membership, enabled/removed state, or
display filename.

## 12. Compatibility Requirements

The Personal Knowledge implementation must preserve:

1. Existing `/embed`, `/query`, context, and delete route paths.
2. Existing multipart names `file_id`, `file`, and `entity_id`.
3. Existing `/query` request and tuple response shapes.
4. Existing normal File Search behavior and per-file `/query` callers.
5. Existing delete handling where LibreChat accepts `404` as already deleted.
6. Existing pgvector distance ordering and configured threshold behavior.
7. Existing File Search artifact format after LibreChat enriches batch results.

The required service hardening is scoped to `POST /query_multiple` behavior
from Section 8 and derived text/Markdown chunk-location metadata within the
existing `/embed` wire contract.

## 13. Acceptance Matrix

| Scenario | Expected result |
|---|---|
| Valid JWT and authorized File IDs | `200` with globally ranked tuples |
| Missing or invalid JWT with `JWT_SECRET` configured | `401` |
| One authorized and one unauthorized File ID | `200`; only authorized chunks may appear |
| All File IDs unknown or outside scope | `200 []` without existence disclosure |
| Duplicate File IDs | Deduplicated before retrieval |
| 50 unique File IDs | Accepted |
| 51 unique File IDs | `422`; LibreChat must not send more than 50 after active source resolution |
| `k` equal to `1` or `50` | Accepted |
| `k` below `1` or above `50` | `422` |
| Blank query or blank File ID | `422` |
| Results distributed across multiple files | One global top-`k`, not per-file quotas |
| No relevant chunks after threshold | `200 []`; no Personal Knowledge citation |
| Returned File ID absent from LibreChat authorized map | Result discarded by LibreChat |
| Existing normal File Search query | Continues using `/query` unchanged |
| Existing embedding caller | Continues using `/embed` multipart contract |
| Adding an embedded upload to Personal Knowledge | Does not call `/embed`, duplicate storage, or create vectors |
| Plain-text or Markdown embedding | Stored chunks derive line ranges and optional section headings |
| Cleanup deletes an absent File ID | `rag_api` may return `404`; LibreChat treats it as complete |

## 14. Source References

- `latuan1/rag_api`
- `rag_api/app/models.py`
- `rag_api/app/middleware.py`
- `rag_api/app/routes/document_routes.py`
- LibreChat `api/app/clients/tools/util/fileSearch.js`
- LibreChat `api/server/services/Files/VectorDB/crud.js`
- LibreChat `packages/api/src/files/rag.ts`
- `docs/knowledge/PERSONAL_KNOWLEDGE_SPEC.md`
- `docs/knowledge/PERSONAL_KNOWLEDGE_ARCHITECTURE.md`
