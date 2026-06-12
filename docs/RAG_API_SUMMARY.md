# Current Implementation Summary

**Repository state reviewed:** June 12, 2026  
**Branch:** `chunking-service-improvement`  
**Audience:** Developers and project maintainers

## Executive Summary

This repository implements an asynchronous FastAPI service for document ingestion,
text extraction, vector indexing, and retrieval. Documents are organized primarily
by `file_id`, which supports LibreChat's file-search and local-knowledge workflows
while remaining usable as a general ID-oriented RAG API.

The service supports PostgreSQL with pgvector and MongoDB Atlas Vector Search,
multiple embedding providers, configurable chunking, bounded batch insertion,
file ownership metadata, optional JWT validation, and structured API errors. The
current feature branch adds the new chunking subsystem and a LibreChat-oriented
local-knowledge contract on top of the existing service.

The branch is six commits ahead of `main`. The committed branch diff affects 42
files with approximately 4,809 additions and 95 deletions. There are also separate
uncommitted workspace changes described under [Workspace State](#workspace-state).

## Architecture

### Application Lifecycle

`main.py` creates the FastAPI application and owns process-level resources:

- A bounded `ThreadPoolExecutor` is created at startup for blocking loader,
  embedding, vector-store, and database operations.
- The async PostgreSQL pool and vector indexes are initialized when pgvector is
  selected.
- Shutdown drains the thread pool, closes the async PostgreSQL pool, and closes
  vector-store resources such as SQLAlchemy engines and MongoDB clients.
- CORS, request logging, security middleware, and shared exception handlers are
  registered centrally.

### Routes

The API is divided into three route groups:

- `app/routes/document_routes.py` provides legacy file ingestion, extraction,
  document management, and retrieval endpoints.
- `app/routes/knowledge_routes.py` provides the LibreChat local-knowledge indexing,
  querying, and deletion contract under `/knowledge`.
- `app/routes/pgvector_routes.py` provides database inspection endpoints that are
  registered only when `DEBUG_RAG_API` is enabled.

### Chunking Pipeline

The chunking implementation is isolated under `app/services/chunking/` and selected
through a factory. Configuration is resolved once from environment variables.

Implemented strategies are:

- `recursive`: LangChain recursive character splitting with configurable size and
  overlap.
- `paragraph`: paragraph-preserving grouping with small-tail merging and recursive
  fallback for oversized paragraphs.
- `auto`: metadata normalization followed by slide-aware, row-aware table, or
  paragraph-aware processing. Failures fall back to recursive chunking.

The default `balanced` preset selects `auto` with a chunk size of 1,200 and overlap
of 120. The `legacy` preset selects recursive splitting with the former 1,500/100
defaults. Explicit environment settings override preset-derived values.

Chunk metadata includes protected `file_id`, `user_id`, and digest values, plus
chunk index, strategy, preset, size, and contextual-prefix state. Loader metadata
is sanitized before merging and cannot overwrite protected fields.

### Embeddings and Vector Stores

Embedding providers currently include OpenAI, Azure OpenAI, Hugging Face,
Hugging Face TEI, Ollama, Amazon Bedrock, Google GenAI, and Google Vertex AI.

The vector-store factory supports:

- pgvector through asynchronous and extended pgvector implementations.
- MongoDB Atlas Vector Search through the Atlas adapter.

Both backends expose the operations needed by the document and knowledge routes,
including similarity search, ID-based access, deletion, and metadata-filtered
deletion. Backend score differences are normalized by the knowledge-query layer;
the legacy distance-threshold option is applied only to pgvector.

### Middleware and Errors

JWT verification is optional and enabled by setting `JWT_SECRET`. Health,
OpenAPI, Swagger, and all `/knowledge/*` routes bypass JWT middleware. Other routes
accept unsigned traffic only when no secret is configured.

HTTP exceptions, request-validation failures, middleware failures, and explicit
route errors use the shared envelope:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Request failed",
    "details": {}
  }
}
```

## Completed Branch Work

### Chunking Service Refactor

The former route-local splitting behavior now delegates to dedicated chunking
services. Preset and strategy configuration, recursive compatibility behavior,
metadata generation, and fallback behavior are covered by focused unit tests.

### Metadata-Aware Automatic Chunking

The branch implements:

- Stable normalization for filenames, one-based page ranges, heading paths,
  slide ranges, sheet names, and row ranges.
- Metadata sanitization and protected system fields.
- Deterministic contextual prefixes derived from reliable source metadata.
- Paragraph-aware grouping and recursive fallback.
- Chunk provenance fields for diagnostics and retrieval analysis.

### Structured File Handling

Automatic chunking can preserve reliable slide boundaries and split pipe-rendered
tables by rows while repeating the table header. CSV/XLS/XLSX loader behavior is
connected to row metadata, and oversized structured content falls back to
recursive splitting where required.

### LibreChat API Contract

The existing file-search endpoints have been documented and refined to use shared
validation and error behavior. The branch also adds local-knowledge endpoints with
typed request models:

- Deterministic knowledge chunk IDs support repeatable indexing.
- Queries enforce owner, tenant, space, document, and status filters.
- Results support minimum relevance, per-document limits, and total limits.
- Deletion uses the same ownership and document metadata boundary.

The detailed wire contract is documented in
[LIBRECHAT_RAG_API_CONTRACT.md](LIBRECHAT_RAG_API_CONTRACT.md).

### Existing Operational and Security Features

The branch retains and integrates earlier repository work:

- Bounded asynchronous and synchronous embedding batches with rollback on failure.
- Unique temporary upload paths to isolate concurrent uploads.
- Path traversal and prefix-bypass validation for local and uploaded files.
- Lazy document loading and cleanup of temporary encoding-conversion files.
- Optional JWT ownership checks for legacy file retrieval.
- Connection-pool, SQLAlchemy engine, MongoDB client, and thread-pool cleanup.
- pgvector schema selection, connection pre-ping/recycle settings, optional
  extension creation, JSONB filtering, and distance thresholds.

## API Inventory

### Health and Document Management

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check service and backing-store availability. |
| `GET` | `/ids` | List indexed file IDs. |
| `GET` | `/documents?ids=...` | Load documents for one or more IDs. |
| `DELETE` | `/documents` | Delete documents by file ID. |
| `GET` | `/documents/{file_id}/context` | Return reconstructed document context. |

### Ingestion and Text Extraction

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/local/embed` | Index a server-local file after path validation. |
| `POST` | `/embed` | Upload and index a file using the legacy contract. |
| `POST` | `/embed-upload` | Upload and index a file with an explicit file ID. |
| `POST` | `/text` | Extract text without creating embeddings. |

### Retrieval

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/query` | Search within one file ID with ownership checks. |
| `POST` | `/query_multiple` | Search across a supplied set of file IDs. |

### Local Knowledge

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/knowledge/index` | Index one pre-chunked knowledge item. |
| `POST` | `/knowledge/query` | Filter and retrieve local-knowledge chunks. |
| `POST` | `/knowledge/delete` | Delete chunks for one knowledge document/file. |

### Debug-Only pgvector Inspection

When `DEBUG_RAG_API=true`, the application also exposes index checks, table and
column inspection, and record inspection under `/test/*`, `/db/*`, and `/records*`.
These endpoints are not registered in normal operation.

## Test Coverage

Static discovery found 111 test functions. The suite covers:

- Chunking configuration, recursive and paragraph strategies, automatic routing,
  metadata sanitization, normalizers, and row-aware table splitting.
- pgvector and MongoDB adapters, filter translation, query-plan regressions, and
  LangChain upgrade guardrails.
- Batch processing, bounded-memory behavior, rollback, queue behavior, and MongoDB
  ID generation.
- Concurrent upload isolation, path traversal, symlink handling, and malformed
  input validation.
- Middleware, shared errors, API models, legacy routes, local-knowledge routes,
  loaders, and application lifecycle behavior.

The test suite was not executed during this review because neither Python nor
pytest is available in the current PowerShell environment. The count above is
therefore a source-level inventory, not a passing-test claim.

## Current Gaps and Risks

### Documentation Drift

- The README does not document the new `CHUNKING_PRESET`, `CHUNKING_STRATEGY`,
  `MIN_CHUNK_SIZE`, `MAX_CHUNK_SIZE`, `PRESERVE_STRUCTURE`, or contextual-prefix
  environment variables.
- The README states that `EMBEDDING_BATCH_SIZE` defaults to `0`, while
  `app/config.py` currently defaults it to `500`.
- The README's top-level feature list understates the implemented embedding
  providers, vector backends, chunking strategies, and knowledge API.

### Authentication Boundary

All `/knowledge/*` routes intentionally bypass JWT middleware. Their privacy
boundary depends on caller-supplied owner and tenant filters and on deployment
controls outside this service. Deployments that expose these routes directly
should confirm that this matches their trust model.

### Partial Specification Coverage

The automatic chunker is a practical subset of the broader improvement
specification. It supports normalized metadata, paragraph grouping, reliable slide
boundaries, pipe-table row splitting, prefixes, and recursive fallback, but it
does not implement every proposed file-type-specific behavior. In particular,
the complete PDF page-span policy, richer heading-aware splitting, generalized
table detection, and all observability proposals remain incomplete or best effort.

The `PRESERVE_STRUCTURE` setting is resolved and exposed in configuration, but the
current strategy implementations do not consistently use it as a behavior switch.

### Runtime Verification

Application startup, imports, and the automated suite remain unverified in the
current shell due to the unavailable Python runtime. Dependency compatibility and
integration behavior should not be inferred solely from static inspection.

## Git Progress

The feature branch contains these six commits after `main`:

| Date | Commit | Summary |
| --- | --- | --- |
| 2026-06-05 | `764fe3d` | Add chunking strategy improvement specification. |
| 2026-06-08 | `8f5029b` | Add staged chunking implementation plans. |
| 2026-06-08 | `9cdd13a` | Add chunking services and preset configuration. |
| 2026-06-08 | `2a5deae` | Add metadata-aware automatic chunking. |
| 2026-06-08 | `5a5a624` | Refine structured-file chunking. |
| 2026-06-12 | `afe5205` | Refine the LibreChat API contract. |

Relative to `main`, the committed work adds the chunking package, local-knowledge
routes, shared errors, contract documentation, and focused tests while modifying
configuration, legacy routes, middleware, vector adapters, and application setup.

## Workspace State

At review time, the following changes were present but not committed:

- `.dockerignore` adds repository, environment, Codex dependency, cache, and local
  development exclusions.
- `.gitignore` adds `.pytest_cache`.
- `docker-compose.yaml` changes the API build to use `Dockerfile.lite`.

These local changes are separate from the committed feature summary above. They
were inspected for reporting purposes and were not modified as part of this
document.

## Related Documentation

- [Chunking Strategy Improvement Specification](CHUNKING_STRATEGY_IMPROVING_SPEC.md)
- [PR 1: Service Refactor Plan](CHUNKING_STRATEGY_PR1_SERVICE_REFACTOR_PLAN.md)
- [PR 2: Metadata and Auto Chunking Plan](CHUNKING_STRATEGY_PR2_METADATA_AUTO_PLAN.md)
- [PR 3: Structured Files Plan](CHUNKING_STRATEGY_PR3_STRUCTURED_FILES_PLAN.md)
- [LibreChat RAG API Contract](LIBRECHAT_RAG_API_CONTRACT.md)

