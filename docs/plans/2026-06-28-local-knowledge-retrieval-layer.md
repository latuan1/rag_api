# Local Knowledge Retrieval Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an additive, trusted-backend retrieval layer for the production Supabase PostgreSQL knowledge database with pgvector that follows `docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md` without changing the existing uploaded-file RAG behavior.

**Architecture:** Keep the current LangChain `langchain_pg_embedding` upload/query pipeline intact. Add a separate local knowledge retrieval service that connects to the production Supabase PostgreSQL knowledge database with pgvector, discovers and validates the active `public.knowledge_chunks` embedding profile, generates a contract-compatible OpenAI-compatible query embedding, calls `public.match_knowledge_chunks(...)`, and exposes authenticated FastAPI endpoints for retrieval and diagnostics.

**Tech Stack:** FastAPI, Pydantic, asyncpg, OpenAI-compatible embeddings API, Supabase PostgreSQL, pgvector, pytest, pytest-asyncio, testcontainers.

---

## Contract and Review Decisions

The authoritative retrieval contract is `docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md`.

This plan incorporates review fixes before implementation:

- `POST /knowledge/retrieve` must use the contract RPC return shape. Do not invent quality fields in semantic retrieval responses unless the DB function is changed.
- Direct document and neighbor reads must include `review_required`, `review_reasons`, `data_quality_flags`, and `warnings`.
- Profile resolution must return a typed profile object and validate stored profile fields independently.
- Profile resolution must happen once per service operation; routes must use the profile returned by that operation.
- Request `match_count` and `match_threshold` must be optional so configured defaults are honored.
- `/knowledge/*` must require backend authentication even when the global app is running without `JWT_SECRET`.
- Route errors must be sanitized; log internals server-side and return stable public errors.
- pgvector vector literals may be string serialized only after validation, and must remain bound SQL parameters.
- The default `google-gemini` profile is valid only when `RETRIEVAL_EMBEDDING_BASE_URL` points at an OpenAI-compatible Gemini endpoint; never silently send Gemini model names to the default OpenAI endpoint.
- The current `embedding_input_version="v1"` retrieval behavior means raw user query text is sent as `input=[query]` through the OpenAI-compatible embeddings request shape.

## Supabase Deployment Decision

Production retrieval targets the Supabase PostgreSQL knowledge database with pgvector. `RETRIEVAL_DATABASE_URL` must be a PostgreSQL DSN copied from the Supabase Dashboard database connection settings. It must not be `SUPABASE_URL`, `SUPABASE_ANON_KEY`, or `SUPABASE_SERVICE_ROLE_KEY`; those values are REST/API credentials, not PostgreSQL connection strings.

Use a direct PostgreSQL connection or Supavisor session mode for the long-running FastAPI service by default. Direct and session-mode PostgreSQL connections use port `5432`. Supavisor transaction mode uses port `6543`, is optional, and requires asyncpg prepared statement caching to be disabled with `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0`.

The production `vector` extension, `public.knowledge_chunks`, and `public.match_knowledge_chunks(...)` objects are managed through versioned SQL migrations such as `supabase/migrations/*.sql`. FastAPI application startup must never create, drop, or alter production Supabase schema objects.

## File Structure

Create:

- `app/services/local_knowledge_retrieval.py`: configuration dataclass, typed profile resolution, OpenAI-compatible embedding client, vector validation/serialization, RPC retrieval, document reads, neighbor reads, output envelopes, typed exceptions, async pool lifecycle.
- `app/routes/local_knowledge_routes.py`: authenticated FastAPI routes and a focused shutdown helper.
- `tests/services/test_local_knowledge_retrieval.py`: unit tests for profile resolution, vector validation, SQL scoping, default application, and output envelopes.
- `tests/test_local_knowledge_routes.py`: route tests for auth, response shape, sanitized errors, and fake-service compatibility.
- `tests/integration/test_local_knowledge_retrieval_pgvector.py`: dedicated pgvector container contract test.

Modify:

- `requirements.txt`: add direct `openai` dependency if `AsyncOpenAI` is imported directly.
- `requirements.lite.txt`: add the same direct `openai` dependency if needed by runtime.
- `app/config.py`: add `RETRIEVAL_*` and `KNOWLEDGE_API_TOKEN` settings without creating network clients at import time.
- `app/models.py`: add Pydantic request/response models for local knowledge retrieval.
- `main.py`: include the new router and call the focused shutdown helper.
- `README.md`: document retrieval config, auth, security boundary, and endpoint examples.

Do not modify:

- Existing `/query`, `/query_multiple`, `/embed`, `/embed-upload`, `/local/embed`, `/documents`, or `/documents/{id}/context` behavior.
- Existing `AsyncPgVector`, `ExtendedPgVector`, or Atlas Mongo vector-store classes.
- `docs/LOCAL_KNOWLEDGE_RETRIEVAL_CONTRACT.md`.

## Public API Design

These endpoints are for trusted backend callers. They must require either:

- `X-Knowledge-API-Key: <KNOWLEDGE_API_TOKEN>` when `KNOWLEDGE_API_TOKEN` is configured, or
- `Authorization: Bearer <JWT>` that has already been authenticated by the existing JWT middleware when `JWT_SECRET` is configured and valid.

If both auth paths are configured, either one may authenticate the request. If neither backend auth path is configured, `/knowledge/*` must return `503 Service Unavailable` with a stable message. If auth is configured but credentials are missing or invalid, `/knowledge/*` must return `401 Unauthorized`. This prevents these endpoints from becoming public when the rest of the app is intentionally permissive for local development.

### `GET /knowledge/profiles`

Returns active profile diagnostics for the configured namespace.

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

### `POST /knowledge/retrieve`

Embeds the query, resolves exactly one active profile for the operation, calls `public.match_knowledge_chunks(...)`, and returns the contract RPC rows.

Request fields:

- `query`: required non-empty string.
- `match_count`: optional integer `1..50`; service uses `RETRIEVAL_DEFAULT_MATCH_COUNT` when omitted.
- `match_threshold`: optional float `-1.0..1.0`; service uses `RETRIEVAL_DEFAULT_MATCH_THRESHOLD` when omitted.
- `filter_document_type`: optional trimmed string; blank values normalize to `null`.

```json
{
  "query": "What is the grade appeal procedure?",
  "match_count": null,
  "match_threshold": null,
  "filter_document_type": null
}
```

Response:

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

### `GET /knowledge/documents/{document_id}?limit=500`

Returns active chunks for one document, scoped by namespace and the resolved profile. `document_id` must be validated with at least `min_length=1` and `max_length=255`; tighten the format further only if the ingested IDs have a documented schema. `limit` is required to be bounded by the model/router as `1..2000` and defaults to `500`.

### `GET /knowledge/documents/{document_id}/neighbors?ordinal=12&window=1`

Returns active chunks around an ordinal, inclusive of the center ordinal. `window` is bounded as `0..10`.

## Task 0: Verify Dependencies and Runtime Images

**Files:**

- Modify: `requirements.txt`
- Modify: `requirements.lite.txt`
- Inspect: `test_requirements.txt`
- Inspect: `Dockerfile`
- Inspect: `Dockerfile.lite`

- [ ] **Step 1: Confirm current dependency state**

Run:

```bash
rg -n "^(openai|asyncpg|pgvector|pytest-asyncio|testcontainers|psycopg2-binary|langchain-openai|pydantic|fastapi)" requirements.txt requirements.lite.txt test_requirements.txt
```

Expected:

- `asyncpg` exists in both runtime requirements files.
- `pgvector` exists in both runtime requirements files.
- `psycopg2-binary` exists in both runtime requirements files.
- `pytest-asyncio` exists in `test_requirements.txt`.
- `testcontainers[postgres]` exists in `test_requirements.txt`.
- `langchain-openai` exists but does not replace a direct `openai` dependency if this feature imports `openai.AsyncOpenAI`.
- `pydantic>=2.10.6,<3` exists in runtime requirements, so `StringConstraints` is supported.
- `fastapi` is present and compatible with the existing Pydantic v2 runtime.

- [ ] **Step 2: Add direct OpenAI dependency if missing**

If `requirements.txt` lacks an explicit `openai` package line, add:

```text
openai>=1.0.0,<2
```

Add the same line to `requirements.lite.txt`. If dependency inspection shows `langchain-openai` requires a narrower OpenAI SDK range, use the narrower range instead. Do not allow OpenAI SDK major version 2 unless the project has explicitly tested it.

- [ ] **Step 3: Verify Docker images install the updated requirement files**

Confirm:

- `Dockerfile` copies and installs `requirements.txt`.
- `Dockerfile.lite` copies and installs `requirements.lite.txt`.

No Dockerfile changes are needed if those lines already exist.

- [ ] **Step 4: Commit**

Run:

```bash
git add requirements.txt requirements.lite.txt
git commit -m "deps: add direct OpenAI dependency for retrieval"
```

Skip this commit if `openai` was already explicitly present and no dependency file changed.

## Task 1: Add Retrieval Configuration

**Files:**

- Modify: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Verify env helper behavior and write environment-independent failing tests**

Inspect `get_env_variable()` in `app/config.py` before adding nullable retrieval settings. It currently returns `default_value` unchanged when the environment variable is missing, but the retrieval config should still use empty-string defaults for nullable strings to avoid relying on `None` behavior in future helper changes:

```python
get_env_variable("RETRIEVAL_EMBEDDING_BASE_URL", "") or None
get_env_variable("RETRIEVAL_EMBEDDING_PROFILE", "") or None
get_env_variable("KNOWLEDGE_API_TOKEN", "") or None
```

Add tests for a focused settings loader instead of reloading all of `app.config`. This avoids unrelated module-level clients and `.env` side effects.

```python
from app.config import load_retrieval_settings


def test_retrieval_settings_defaults():
    values = {}

    def fake_get_env(name, default=None, required=False):
        return values.get(name, default)

    settings = load_retrieval_settings(fake_get_env)

    assert settings.dataset_namespace == "vinuni-policy"
    assert settings.embedding_provider == "google-gemini"
    assert settings.embedding_model == "gemini-embedding-001"
    assert settings.embedding_dimensions == 1536
    assert settings.embedding_input_version == "v1"
    assert settings.embedding_profile is None
    assert settings.default_match_count == 10
    assert settings.default_match_threshold == 0.0
    assert settings.database_command_timeout == 30.0
    assert settings.database_statement_cache_size == 100
    assert settings.embedding_timeout_seconds == 30.0
    assert settings.knowledge_api_token is None
```

Add explicit tests for Supavisor transaction-mode support and invalid integer input:

```python
def test_retrieval_settings_allows_zero_statement_cache_size():
    values = {"RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE": "0"}

    def fake_get_env(name, default=None, required=False):
        return values.get(name, default)

    settings = load_retrieval_settings(fake_get_env)

    assert settings.database_statement_cache_size == 0


def test_retrieval_settings_rejects_invalid_statement_cache_size():
    values = {"RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE": "not-an-int"}

    def fake_get_env(name, default=None, required=False):
        return values.get(name, default)

    with pytest.raises(ValueError):
        load_retrieval_settings(fake_get_env)
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
pytest tests/test_config.py::test_retrieval_settings_defaults -v
```

Expected: fails because the retrieval config names are not defined yet.

- [ ] **Step 3: Implement config values**

In `app/config.py`, import `dataclass` from `dataclasses`. After `DSN` and before embedding initialization, add a small settings loader and expose module constants from it:

```python
@dataclass(frozen=True)
class RetrievalSettings:
    database_url: str
    embedding_api_key: str
    embedding_base_url: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_input_version: str
    dataset_namespace: str
    embedding_profile: str | None
    default_match_count: int
    default_match_threshold: float
    database_command_timeout: float
    database_statement_cache_size: int
    embedding_timeout_seconds: float
    knowledge_api_token: str | None


def load_retrieval_settings(env_getter=get_env_variable) -> RetrievalSettings:
    return RetrievalSettings(
        database_url=env_getter("RETRIEVAL_DATABASE_URL", DSN),
        embedding_api_key=env_getter("RETRIEVAL_EMBEDDING_API_KEY", ""),
        embedding_base_url=env_getter("RETRIEVAL_EMBEDDING_BASE_URL", "") or None,
        embedding_provider=env_getter("RETRIEVAL_EMBEDDING_PROVIDER", "google-gemini"),
        embedding_model=env_getter("RETRIEVAL_EMBEDDING_MODEL", "gemini-embedding-001"),
        embedding_dimensions=int(env_getter("RETRIEVAL_EMBEDDING_DIMENSIONS", "1536")),
        embedding_input_version=env_getter("RETRIEVAL_EMBEDDING_INPUT_VERSION", "v1"),
        dataset_namespace=env_getter("RETRIEVAL_DATASET_NAMESPACE", "vinuni-policy"),
        embedding_profile=env_getter("RETRIEVAL_EMBEDDING_PROFILE", "") or None,
        default_match_count=int(env_getter("RETRIEVAL_DEFAULT_MATCH_COUNT", "10")),
        default_match_threshold=float(env_getter("RETRIEVAL_DEFAULT_MATCH_THRESHOLD", "0.0")),
        database_command_timeout=float(env_getter("RETRIEVAL_DATABASE_COMMAND_TIMEOUT", "30")),
        database_statement_cache_size=int(env_getter("RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE", "100")),
        embedding_timeout_seconds=float(env_getter("RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS", "30")),
        knowledge_api_token=env_getter("KNOWLEDGE_API_TOKEN", "") or None,
    )


retrieval_settings = load_retrieval_settings()
RETRIEVAL_DATABASE_URL = retrieval_settings.database_url
RETRIEVAL_EMBEDDING_API_KEY = retrieval_settings.embedding_api_key
RETRIEVAL_EMBEDDING_BASE_URL = retrieval_settings.embedding_base_url
RETRIEVAL_EMBEDDING_PROVIDER = retrieval_settings.embedding_provider
RETRIEVAL_EMBEDDING_MODEL = retrieval_settings.embedding_model
RETRIEVAL_EMBEDDING_DIMENSIONS = retrieval_settings.embedding_dimensions
RETRIEVAL_EMBEDDING_INPUT_VERSION = retrieval_settings.embedding_input_version
RETRIEVAL_DATASET_NAMESPACE = retrieval_settings.dataset_namespace
RETRIEVAL_EMBEDDING_PROFILE = retrieval_settings.embedding_profile
RETRIEVAL_DEFAULT_MATCH_COUNT = retrieval_settings.default_match_count
RETRIEVAL_DEFAULT_MATCH_THRESHOLD = retrieval_settings.default_match_threshold
RETRIEVAL_DATABASE_COMMAND_TIMEOUT = retrieval_settings.database_command_timeout
RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE = retrieval_settings.database_statement_cache_size
RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS = retrieval_settings.embedding_timeout_seconds
KNOWLEDGE_API_TOKEN = retrieval_settings.knowledge_api_token
```

Do not instantiate an OpenAI client or database connection in `app/config.py`. Document in code comments or README text, not runtime branching, that Supavisor transaction-mode deployments on port `6543` must set `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0`.

- [ ] **Step 4: Run config tests and verify pass**

Run:

```bash
pytest tests/test_config.py -v
```

Expected: all config tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: add local knowledge retrieval config"
```

## Task 2: Add Retrieval API Models

**Files:**

- Modify: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

Add:

```python
from pydantic import ValidationError

from app.models import KnowledgeRetrievalRequest


def test_knowledge_retrieval_request_defaults_defer_to_service_config():
    body = KnowledgeRetrievalRequest(query="What is the grade appeal procedure?")

    assert body.query == "What is the grade appeal procedure?"
    assert body.match_count is None
    assert body.match_threshold is None
    assert body.filter_document_type is None


def test_knowledge_retrieval_request_rejects_empty_query():
    try:
        KnowledgeRetrievalRequest(query="")
    except ValidationError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("empty query must fail validation")


def test_knowledge_retrieval_request_rejects_whitespace_query():
    try:
        KnowledgeRetrievalRequest(query="   ")
    except ValidationError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("whitespace-only query must fail validation")


def test_knowledge_retrieval_request_rejects_out_of_range_threshold():
    try:
        KnowledgeRetrievalRequest(query="hello", match_threshold=1.5)
    except ValidationError as exc:
        assert "match_threshold" in str(exc)
    else:
        raise AssertionError("threshold outside [-1.0, 1.0] must fail")


def test_blank_filter_document_type_normalizes_to_none():
    body = KnowledgeRetrievalRequest(query="hello", filter_document_type="   ")

    assert body.filter_document_type is None
```

- [ ] **Step 2: Run model tests and verify failure**

Run:

```bash
pytest tests/test_models.py -v
```

Expected: fails because `KnowledgeRetrievalRequest` does not exist or defaults are wrong.

- [ ] **Step 3: Implement models**

In `app/models.py`, use Pydantic `Field` and `StringConstraints`. Task 0 verifies this repo uses Pydantic v2; if that changes in the future, use a compatible `constr(...)` or validator instead.

```python
from typing import Annotated
from pydantic import StringConstraints, field_validator


NonEmptyQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

OptionalTrimmedString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class KnowledgeRetrievalRequest(BaseModel):
    query: NonEmptyQuery
    match_count: Optional[int] = Field(default=None, ge=1, le=50)
    match_threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    filter_document_type: Optional[OptionalTrimmedString] = None

    @field_validator("filter_document_type", mode="before")
    @classmethod
    def normalize_blank_filter_document_type(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class KnowledgeProfile(BaseModel):
    embedding_profile: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_input_version: str
    active_chunks: int


class KnowledgeProfilesResponse(BaseModel):
    dataset_namespace: str
    profiles: List[KnowledgeProfile]


class KnowledgeRetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    section_id: str
    ordinal: int
    content: str
    embedding_content: str
    policy_title: str
    reference_number: Optional[str] = None
    document_type: Optional[str] = None
    heading: str
    heading_path: List[str]
    url_source: str
    similarity: float


class KnowledgeRetrievalResponse(BaseModel):
    dataset_namespace: str
    embedding_profile: str
    results: List[KnowledgeRetrievalResult]


class KnowledgeDocumentChunk(BaseModel):
    chunk_id: str
    ordinal: int
    content: str
    heading: str
    heading_path: List[str]
    source_line_spans: List[dict[str, Any]]
    review_required: bool
    review_reasons: List[str]
    data_quality_flags: List[str]
    warnings: List[str]


class KnowledgeDocumentResponse(BaseModel):
    dataset_namespace: str
    embedding_profile: str
    document_id: str
    chunks: List[KnowledgeDocumentChunk]
```

Do not add quality metadata to `KnowledgeRetrievalResult`; the current RPC contract does not return it.

- [ ] **Step 4: Run model tests and verify pass**

Run:

```bash
pytest tests/test_models.py -v
```

Expected: all model tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add local knowledge retrieval models"
```

## Task 3: Implement Retrieval Service

**Files:**

- Create: `app/services/local_knowledge_retrieval.py`
- Test: `tests/services/test_local_knowledge_retrieval.py`

- [ ] **Step 1: Write failing service tests**

Create tests covering:

- `normalize_asyncpg_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"`.
- `normalize_asyncpg_dsn("postgresql+psycopg2://u:p@h:5432/db?sslmode=require") == "postgresql://u:p@h:5432/db?sslmode=require"` so Supabase TLS parameters are preserved.
- `validated_vector_to_pg_literal([0.1] * 1536, 1536)` returns a pgvector literal.
- wrong dimension and non-finite values raise `RetrievalEmbeddingError`.
- discovery SQL selects `embedding_input_version` and scopes by `dataset_namespace` plus `is_active is true`.
- no active profiles fail closed.
- multiple active profiles without explicit config fail closed.
- configured inactive profile fails closed.
- provider mismatch fails closed.
- model mismatch fails closed.
- dimension mismatch fails closed.
- input-version mismatch fails closed.
- base service config rejects empty `database_url`.
- base service config rejects `embedding_dimensions != 1536`.
- base service config rejects unsupported `embedding_input_version != "v1"`.
- service config rejects `default_match_count` outside `1..50`.
- service config rejects `default_match_threshold` outside `-1.0..1.0`.
- service config rejects `database_command_timeout <= 0`.
- service config rejects `embedding_timeout_seconds <= 0`.
- service config accepts positive float timeout values.
- service config rejects `database_statement_cache_size < 0`.
- service config passes `database_command_timeout` into `asyncpg.create_pool`.
- service config passes `database_statement_cache_size` into `asyncpg.create_pool`.
- service accepts `database_statement_cache_size=0` for Supavisor transaction mode.
- embedding client construction passes `embedding_timeout_seconds` to `AsyncOpenAI`.
- semantic retrieval rejects missing `embedding_api_key`.
- semantic retrieval rejects non-OpenAI provider slugs without `embedding_base_url`, including default `google-gemini`.
- semantic retrieval rejects `embedding_model == "gemini-embedding-001"` without `embedding_base_url`, even if the provider slug is misconfigured as `openai`.
- database-only service operations can be constructed and used without embedding API key/base URL.
- profile discovery, `fetch_document()`, and `fetch_neighbors()` do not call `validate_embedding_config()`.
- `retrieve()` fails closed when embedding config is incomplete while database-only operations still work.
- one configured profile string with multiple active metadata groups fails closed.
- `dataset_namespace` property returns `config.dataset_namespace`.
- constructing `LocalKnowledgeRetrievalService(config)` leaves `_pool is None` and does not open a database connection or run schema DDL.
- repeated `embed_query()` calls reuse one stored embedding client.
- embedding client construction happens exactly once for repeated `embed_query()` calls.
- `close()` closes the asyncpg pool, closes the embedding HTTP client, and resets service state.
- `close()` closes the exact stored embedding client and resets `_embedding_client` to `None`.
- fake DB failures are wrapped as `RetrievalDatabaseError`.
- `retrieve()` returns `RetrievalOutput(profile, rows)` and calls `resolve_profile()` once.
- `retrieve()` applies configured defaults when request values are `None`.
- `fetch_document()` scopes by namespace/profile/active/document and returns `DocumentOutput`.
- `fetch_neighbors()` scopes by namespace/profile/active/document and uses bounded inclusive ordinals.
- vector literal is passed as a bind argument, not interpolated into SQL.

Minimum fake helpers:

```python
class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.close_called = False

    def acquire(self):
        return FakeAcquire(self.conn)

    async def close(self):
        self.close_called = True


class FakeEmbeddingClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows
```

- [ ] **Step 2: Run service tests and verify failure**

Run:

```bash
pytest tests/services/test_local_knowledge_retrieval.py -v
```

Expected: fails because the service module does not exist.

- [ ] **Step 3: Implement service types**

Create `app/services/local_knowledge_retrieval.py` with these public types:

```python
@dataclass(frozen=True)
class LocalKnowledgeConfig:
    database_url: str
    dataset_namespace: str
    embedding_api_key: str
    embedding_base_url: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_input_version: str
    embedding_profile: str | None
    default_match_count: int
    default_match_threshold: float
    database_command_timeout: float
    database_statement_cache_size: int
    embedding_timeout_seconds: float


@dataclass(frozen=True)
class ResolvedEmbeddingProfile:
    embedding_profile: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_input_version: str
    active_chunks: int


@dataclass(frozen=True)
class RetrievalOutput:
    profile: ResolvedEmbeddingProfile
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class DocumentOutput:
    profile: ResolvedEmbeddingProfile
    rows: list[dict[str, Any]]
```

Add exception classes:

```python
class RetrievalConfigurationError(RuntimeError):
    """Raised when retrieval config does not match active database state."""


class RetrievalEmbeddingError(RuntimeError):
    """Raised when query embedding generation or validation fails."""


class RetrievalDatabaseError(RuntimeError):
    """Raised when the knowledge database cannot be queried."""
```

- [ ] **Step 4: Implement service properties and config validation**

Expose the namespace without requiring routes to know the config structure:

```python
@property
def dataset_namespace(self) -> str:
    return self.config.dataset_namespace
```

Implement `validate_base_config()` and call it in `__init__`. This validation must allow profile diagnostics and direct document reads to work before embedding credentials are configured:

```python
def validate_base_config(config: LocalKnowledgeConfig) -> None:
    if not config.database_url:
        raise RetrievalConfigurationError(
            "retrieval database URL is not configured"
        )
    if config.embedding_dimensions != 1536:
        raise RetrievalConfigurationError(
            "local knowledge retrieval requires exactly 1536 embedding dimensions"
        )
    if config.embedding_input_version != "v1":
        raise RetrievalConfigurationError(
            "unsupported retrieval embedding input version"
        )
    if not 1 <= config.default_match_count <= 50:
        raise RetrievalConfigurationError(
            "default match count must be between 1 and 50"
        )
    if not -1.0 <= config.default_match_threshold <= 1.0:
        raise RetrievalConfigurationError(
            "default match threshold must be between -1.0 and 1.0"
        )
    if config.database_command_timeout <= 0:
        raise RetrievalConfigurationError(
            "database command timeout must be greater than 0"
        )
    if config.embedding_timeout_seconds <= 0:
        raise RetrievalConfigurationError(
            "embedding timeout must be greater than 0"
        )
    if config.database_statement_cache_size < 0:
        raise RetrievalConfigurationError(
            "database statement cache size must be greater than or equal to 0"
        )
```

Implement `validate_embedding_config()` and call it only from `embed_query()` or `retrieve()`:

```python
def validate_embedding_config(config: LocalKnowledgeConfig) -> None:
    if not config.embedding_api_key:
        raise RetrievalConfigurationError(
            "retrieval embedding API key is not configured"
        )
    if config.embedding_provider != "openai" and not config.embedding_base_url:
        raise RetrievalConfigurationError(
            "an OpenAI-compatible embedding base URL is required for non-OpenAI providers"
        )
    if config.embedding_model == "gemini-embedding-001" and not config.embedding_base_url:
        raise RetrievalConfigurationError(
            "Gemini embedding model requires an explicit OpenAI-compatible base URL"
        )
```

This prevents the default `google-gemini` / `gemini-embedding-001` profile from being sent to OpenAI's default endpoint when `RETRIEVAL_EMBEDDING_BASE_URL` is missing.

- [ ] **Step 5: Implement DSN and vector helpers**

Implement:

```python
def normalize_asyncpg_dsn(value: str) -> str:
    return (
        value.replace("postgresql+psycopg2://", "postgresql://", 1)
        .replace("postgresql+asyncpg://", "postgresql://", 1)
    )


def validate_query_embedding(values: Iterable[float], expected_dimensions: int) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != expected_dimensions:
        raise RetrievalEmbeddingError(
            f"query embedding must have {expected_dimensions} dimensions"
        )
    if not all(math.isfinite(value) for value in vector):
        raise RetrievalEmbeddingError("query embedding must contain only finite values")
    return vector


def validated_vector_to_pg_literal(values: Iterable[float], expected_dimensions: int) -> str:
    vector = validate_query_embedding(values, expected_dimensions)
    return "[" + ",".join(str(value) for value in vector) + "]"
```

Do not parse, reconstruct, or drop DSN query parameters; Supabase Dashboard DSNs may include TLS parameters such as `?sslmode=require`. Use `validated_vector_to_pg_literal()` everywhere a pgvector literal is needed.

- [ ] **Step 6: Implement pool lifecycle, DB wrapper, and close**

In the service constructor:

```python
self._pool = pool
self._pool_lock = asyncio.Lock()
self._embedding_client: AsyncOpenAI | None = None
self._embedding_client_lock = asyncio.Lock()
```

In `get_pool()`:

```python
if self._pool is not None:
    return self._pool
async with self._pool_lock:
    if self._pool is None:
        self._pool = await asyncpg.create_pool(
            dsn=normalize_asyncpg_dsn(self.config.database_url),
            command_timeout=self.config.database_command_timeout,
            statement_cache_size=self.config.database_statement_cache_size,
        )
return self._pool
```

`database_statement_cache_size` must default to `100` for direct or Supavisor session-mode deployments. Set it to `0` only for Supavisor transaction mode on port `6543`, because transaction pooling does not support prepared statement reuse across transactions.

Add `_fetch()` so database failures are mapped consistently:

```python
async def _fetch(self, query: str, *args: Any) -> list[Any]:
    try:
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)
    except RetrievalConfigurationError:
        raise
    except Exception as exc:
        raise RetrievalDatabaseError("local knowledge database query failed") from exc
```

All database reads in the service must use `_fetch()`.

Add `close()`:

```python
async def close(self) -> None:
    errors: list[Exception] = []

    async with self._pool_lock:
        pool = self._pool
        self._pool = None

    async with self._embedding_client_lock:
        client = self._embedding_client
        self._embedding_client = None

    if pool is not None:
        try:
            await pool.close()
        except Exception as exc:
            errors.append(exc)

    if client is not None:
        try:
            close_method = getattr(client, "aclose", None)
            if close_method is None:
                close_method = getattr(client, "close", None)
            if close_method is not None:
                result = close_method()
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:
            errors.append(exc)

    if errors:
        raise RuntimeError("one or more retrieval resources failed to close") from errors[0]
```

Import `inspect`. During implementation, verify the installed OpenAI SDK exposes `aclose()` or `close()` and keep the compatibility branch if both are possible across supported versions. Unit tests must prove one cleanup failure does not prevent the other resource from being closed and that service state is reset.

- [ ] **Step 7: Implement active profile discovery**

The SQL must include `embedding_input_version`:

```sql
select
  embedding_profile,
  embedding_provider,
  embedding_model,
  embedding_dimensions,
  embedding_input_version,
  count(*)::int as active_chunks
from public.knowledge_chunks
where dataset_namespace = $1
  and is_active is true
group by
  embedding_profile,
  embedding_provider,
  embedding_model,
  embedding_dimensions,
  embedding_input_version
order by active_chunks desc, embedding_profile asc
```

Return `list[ResolvedEmbeddingProfile]`.

- [ ] **Step 8: Implement profile resolution**

Rules:

- If `config.embedding_profile` is set, it must match one active discovered profile.
- If `config.embedding_profile` is set and matches more than one discovered metadata group, fail closed with `RetrievalConfigurationError`.
- If unset, exactly one active discovered profile must exist.
- Validate `embedding_provider`, `embedding_model`, `embedding_dimensions`, and `embedding_input_version` independently against config.
- Return the `ResolvedEmbeddingProfile`.
- Do not reconstruct or validate by hardcoded `:v1` suffix.

- [ ] **Step 9: Implement OpenAI-compatible query embedding**

Support only OpenAI-compatible embedding transport for v1. `config.embedding_provider` is a stored profile slug, not a provider factory selector. For `embedding_input_version="v1"`, embed the raw user query text using this exact request shape:

```python
async def get_embedding_client(self) -> AsyncOpenAI:
    if self._embedding_client is not None:
        return self._embedding_client
    async with self._embedding_client_lock:
        if self._embedding_client is None:
            created = AsyncOpenAI(
                api_key=self.config.embedding_api_key,
                base_url=self.config.embedding_base_url,
                timeout=self.config.embedding_timeout_seconds,
            )
            self._embedding_client = created
        return self._embedding_client


async def embed_query(self, query: str) -> list[float]:
    validate_embedding_config(self.config)
    client = await self.get_embedding_client()
    response = await client.embeddings.create(
        model=self.config.embedding_model,
        input=[query],
        dimensions=self.config.embedding_dimensions,
    )
    # Extract the provider vector from response.data[0].embedding, then validate it.
```

Then validate with `validate_query_embedding(...)`.

If the provider call fails or returns malformed data, raise `RetrievalEmbeddingError` with a stable internal message. Preserve traceback through exception chaining for logs, not client responses.

- [ ] **Step 10: Implement retrieval and direct reads**

`retrieve(...)` must:

- resolve the profile once;
- embed and validate the query;
- pass vector literal as `$1`, never SQL interpolation;
- use configured defaults when `match_count` or `match_threshold` are `None`;
- return `RetrievalOutput(profile=profile, rows=rows)`.

`fetch_document(document_id, limit)` must:

- resolve the profile once;
- query `public.knowledge_chunks`;
- scope by `dataset_namespace`, `embedding_profile`, `is_active is true`, and `document_id`;
- order by `ordinal asc`;
- apply `limit`;
- return `DocumentOutput(profile=profile, rows=rows)`.

`fetch_neighbors(document_id, ordinal, window)` must:

- resolve the profile once;
- use `start_ordinal = max(0, ordinal - window)` and `end_ordinal = ordinal + window`;
- scope by `dataset_namespace`, `embedding_profile`, `is_active is true`, `document_id`, and ordinal range;
- return `DocumentOutput(profile=profile, rows=rows)`.

- [ ] **Step 11: Run service tests and verify pass**

Run:

```bash
pytest tests/services/test_local_knowledge_retrieval.py -v
```

Expected: all service tests pass.

- [ ] **Step 12: Commit**

Run:

```bash
git add app/services/local_knowledge_retrieval.py tests/services/test_local_knowledge_retrieval.py
git commit -m "feat: add local knowledge retrieval service"
```

## Task 4: Add Authenticated FastAPI Routes

**Files:**

- Create: `app/routes/local_knowledge_routes.py`
- Modify: `app/middleware.py`
- Modify: `main.py`
- Test: `tests/test_local_knowledge_routes.py`

- [ ] **Step 1: Write failing route tests**

Route tests must cover:

- unauthenticated `/knowledge/profiles` returns `503` when neither `KNOWLEDGE_API_TOKEN` nor JWT-authenticated request state is available;
- unauthenticated `/knowledge/profiles` returns `401` when either `KNOWLEDGE_API_TOKEN` or `JWT_SECRET` is configured but credentials are missing or invalid;
- backend token auth succeeds with `X-Knowledge-API-Key: <token>`;
- JWT-authenticated request state succeeds when `KNOWLEDGE_API_TOKEN` is also configured and `X-Knowledge-API-Key` is missing or invalid;
- fake service exposes `dataset_namespace` and returns service envelopes;
- retrieval route uses `output.profile.embedding_profile` from the same operation;
- route does not call `resolve_profile()` separately;
- configuration errors return sanitized `503`;
- embedding/provider errors return sanitized `502` or `503`;
- response bodies never contain raw exception text.
- service factory configuration errors are sanitized when `get_knowledge_service()` itself raises.
- blank `filter_document_type` is normalized to `None`.
- invalid `document_id` values are rejected by route validation.

Fake service shape:

```python
from types import SimpleNamespace


class FakeKnowledgeService:
    dataset_namespace = "vinuni-policy"

    async def discover_active_profiles(self):
        return [
            SimpleNamespace(
                embedding_profile="google-gemini:gemini-embedding-001:1536:v1",
                embedding_provider="google-gemini",
                embedding_model="gemini-embedding-001",
                embedding_dimensions=1536,
                embedding_input_version="v1",
                active_chunks=458,
            )
        ]

    async def retrieve(self, **kwargs):
        profile = (await self.discover_active_profiles())[0]
        return SimpleNamespace(profile=profile, rows=[...])
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```bash
pytest tests/test_local_knowledge_routes.py -v
```

Expected: fails because route module does not exist.

- [ ] **Step 3: Implement backend auth dependency**

Inspect `app/middleware.py` before implementing auth. In the current repo, the middleware applies to all paths except `/docs`, `/openapi.json`, and `/health`; it decodes `Authorization: Bearer <JWT>` when `JWT_SECRET` is configured and stores the decoded payload at `request.state.user`.

Add a small shared helper to `app/middleware.py` so the middleware and knowledge route use the same JWT configuration source:

```python
def is_jwt_auth_configured() -> bool:
    return bool(os.getenv("JWT_SECRET"))
```

Update `security_middleware()` to call `is_jwt_auth_configured()` for the configured check, while still reading the secret value for `jwt.decode(...)` from the same environment source. Do not introduce a separate `JWT_SECRET` config constant unless the middleware is migrated to use it too.

Use a separate API-key header so a backend token is not decoded or rejected by JWT middleware before the route dependency runs. In `app/routes/local_knowledge_routes.py`, implement:

```python
from app.middleware import is_jwt_auth_configured


async def require_knowledge_auth(request: Request) -> None:
    auth_configured = False

    if KNOWLEDGE_API_TOKEN:
        auth_configured = True
        provided_token = request.headers.get("X-Knowledge-API-Key", "")
        if hmac.compare_digest(provided_token, KNOWLEDGE_API_TOKEN):
            return

    if is_jwt_auth_configured():
        auth_configured = True
        # app/middleware.py stores decoded authenticated JWT payloads here.
        user = getattr(request.state, "user", None)
        if user is not None:
            return

    if not auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local knowledge retrieval authentication is not configured",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
    )
```

Register router with:

```python
router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_knowledge_auth)],
)
```

- [ ] **Step 4: Implement service factory and close helper**

Factory must build `LocalKnowledgeConfig` from `app.config` values, including `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE`, and expose:

```python
async def close_knowledge_service() -> None:
    global _knowledge_service
    if _knowledge_service is not None:
        await _knowledge_service.close()
        _knowledge_service = None
```

- [ ] **Step 5: Implement routes using service envelopes**

Routes must use:

```python
from dataclasses import asdict

try:
    service = get_knowledge_service()
    output = await service.retrieve(...)
    return {
        "dataset_namespace": service.dataset_namespace,
        "embedding_profile": output.profile.embedding_profile,
        "results": output.rows,
    }
except (RetrievalConfigurationError, RetrievalDatabaseError) as exc:
    logger.exception("Local knowledge retrieval unavailable")
    raise sanitized_503() from exc
except RetrievalEmbeddingError as exc:
    logger.exception("Local knowledge embedding unavailable")
    raise sanitized_502_or_503() from exc
```

Do the same pattern for document and neighbor routes.

For `GET /knowledge/profiles`, convert profile dataclasses explicitly with `asdict(profile)` or construct `KnowledgeProfile` models directly; do not rely on implicit dataclass serialization.

For document routes, validate IDs and limits with FastAPI parameters:

```python
document_id: str = Path(..., min_length=1, max_length=255)
limit: int = Query(500, ge=1, le=2000)
```

Do not call `service.resolve_profile()` in routes.

- [ ] **Step 6: Sanitize errors**

Map errors:

- `RetrievalConfigurationError` and `RetrievalDatabaseError` -> `503 Service Unavailable`.
- `RetrievalEmbeddingError` -> `502 Bad Gateway` or `503 Service Unavailable`.

Return stable details:

```json
{"detail": "Local knowledge retrieval is unavailable"}
```

Log the internal exception with `logger.exception(...)`.

- [ ] **Step 7: Include router and shutdown helper**

In `main.py`, import `local_knowledge_routes`, include the router after `document_routes.router`, and call `await local_knowledge_routes.close_knowledge_service()` during shutdown before thread-pool shutdown.

Do not test full lifespan just to verify this helper; use a focused route-module test for `close_knowledge_service()`.

- [ ] **Step 8: Run route tests and verify pass**

Run:

```bash
pytest tests/test_local_knowledge_routes.py -v
```

Expected: all route tests pass.

- [ ] **Step 9: Commit**

Run:

```bash
git add app/routes/local_knowledge_routes.py main.py tests/test_local_knowledge_routes.py
git commit -m "feat: expose authenticated local knowledge routes"
```

## Task 5: Verify Supabase Database Contract

**Files:**

- Inspect: `supabase/migrations/*.sql`.
- Create when contract drift is found and separately approved: `supabase/migrations/<timestamp>_align_knowledge_retrieval_contract.sql`.
- Optionally modify deployment verification notes or checklist.

No migration file is created when the production contract already matches this plan. If drift exists, create a reviewed migration but do not apply it to production automatically.

- [ ] **Step 1: Confirm production DSN source**

Before running checks, confirm `RETRIEVAL_DATABASE_URL` is a PostgreSQL DSN from the Supabase Dashboard database connection settings. It must not be `SUPABASE_URL`, `SUPABASE_ANON_KEY`, or `SUPABASE_SERVICE_ROLE_KEY`.

Use direct connection or Supavisor session mode on port `5432` by default. If the DSN uses Supavisor transaction mode on port `6543`, set `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0` in the application environment.

- [ ] **Step 2: Run non-destructive extension and schema checks**

Use a trusted backend shell or migration workstation with read access to the production Supabase PostgreSQL database. Run read-only catalog checks only:

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

- `vector` exists and is installed in the schema used by the retrieval contract, currently `extensions`.
- `public.knowledge_chunks` exists.
- `public.knowledge_chunks.embedding` has base type `vector`, dimension `1536`, and a formatted type such as `extensions.vector(1536)` or `vector(1536)` depending on `search_path`.

- [ ] **Step 3: Verify RPC signature and return columns**

Run read-only checks for the retrieval function:

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

Expected signature:

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

- [ ] **Step 4: Verify active profile data**

Run:

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

Bind `$1` to the configured `RETRIEVAL_DATASET_NAMESPACE`; the current default is `vinuni-policy`. Expected: at least one active profile exists, dimensions are `1536`, and the chosen retrieval configuration uses exactly one active profile.

- [ ] **Step 5: Handle drift through migrations only**

If any production object is missing or outdated, create a versioned SQL migration under `supabase/migrations/*.sql`. Do not add startup-time schema creation or alteration to FastAPI, route modules, or service constructors.

Migration application is a separate gated deployment action:

1. Review the SQL for forward safety, rollback implications, and destructive changes.
2. Apply it first to staging or a disposable database.
3. Run the contract checks again.
4. Apply it to production only through the established deployment process.

Do not automatically apply destructive production migrations from this verification task.

- [ ] **Step 6: Record deployment gate result**

Record in deployment notes whether the Supabase contract verification passed, which connection mode was used, and whether `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0` was required for port `6543`.

## Task 6: Add pgvector Contract Integration Test

**Files:**

- Create: `tests/integration/test_local_knowledge_retrieval_pgvector.py`

- [ ] **Step 1: Write dedicated container fixture**

Use an explicit pgvector container in this test module:

```python
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="module")
def knowledge_pg_container():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg2") as container:
        yield container
```

Create the SQLAlchemy setup engine from `knowledge_pg_container.get_connection_url()` and create the service DSN from that same URL with driver suffix normalized by the service.

- [ ] **Step 2: Drop and recreate schema**

Inside the dedicated test container:

```sql
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
DROP FUNCTION IF EXISTS public.match_knowledge_chunks(
  extensions.vector(1536), text, text, float, int, text
);
DROP TABLE IF EXISTS public.knowledge_chunks CASCADE;
CREATE TABLE public.knowledge_chunks (...);
```

Do not use `CREATE TABLE IF NOT EXISTS` for `knowledge_chunks` in this integration test.

- [ ] **Step 3: Create contract-shaped RPC**

Create `public.match_knowledge_chunks(...)` with the same return columns as the contract:

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

The function must filter by namespace, profile, active rows, optional `filter_document_type`, threshold, and clamped match count.

- [ ] **Step 4: Seed active and inactive rows**

Seed:

- one active row for `vinuni-policy` and `google-gemini:gemini-embedding-001:1536:v1`;
- one inactive row for the same namespace/profile;
- one active row for another namespace or profile.

- [ ] **Step 5: Test service retrieval and direct reads**

Tests:

- `retrieve()` returns only the active row for the configured namespace/profile.
- `fetch_document()` returns active rows only and includes quality fields.
- `fetch_neighbors()` respects inclusive ordinal range.
- profile discovery sees `embedding_input_version`.

Use a shared config factory in pgvector integration tests, then patch query embedding generation in every test that calls `retrieve()`:

```python
def make_config(**overrides) -> LocalKnowledgeConfig:
    values = {
        "database_url": "postgresql://test:test@localhost:5432/test",
        "dataset_namespace": "vinuni-policy",
        "embedding_api_key": "test-key",
        "embedding_base_url": "https://example.invalid/openai/",
        "embedding_provider": "google-gemini",
        "embedding_model": "gemini-embedding-001",
        "embedding_dimensions": 1536,
        "embedding_input_version": "v1",
        "embedding_profile": None,
        "default_match_count": 10,
        "default_match_threshold": 0.0,
        "database_command_timeout": 30.0,
        "database_statement_cache_size": 100,
        "embedding_timeout_seconds": 30.0,
    }
    values.update(overrides)
    return LocalKnowledgeConfig(**values)


service = LocalKnowledgeRetrievalService(
    make_config(database_url=service_dsn)
)


async def fake_embed_query(query: str) -> list[float]:
    return [0.1] * 1536


monkeypatch.setattr(service, "embed_query", fake_embed_query)
```

The pgvector integration test validates database/RPC behavior only. It must not call external embedding APIs or require a real retrieval embedding API key. Provider-client behavior belongs in mocked unit tests.

- [ ] **Step 6: Run integration test**

Run:

```bash
pytest tests/integration/test_local_knowledge_retrieval_pgvector.py -v
```

Expected: passes when Docker/testcontainers can start `pgvector/pgvector:pg16`. If Docker is unavailable, record it as an environmental limitation.

- [ ] **Step 7: Commit**

Run:

```bash
git add tests/integration/test_local_knowledge_retrieval_pgvector.py
git commit -m "test: cover local knowledge pgvector contract"
```

## Task 7: Document Configuration and Usage

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Add README retrieval section**

Document:

- trusted-backend-only intent;
- `/knowledge/*` auth requirement;
- `KNOWLEDGE_API_TOKEN`;
- `X-Knowledge-API-Key` authentication for backend tokens;
- `RETRIEVAL_*` variables including `RETRIEVAL_EMBEDDING_INPUT_VERSION`;
- `RETRIEVAL_DATABASE_COMMAND_TIMEOUT`, `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE`, and `RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS`;
- how to obtain `RETRIEVAL_DATABASE_URL` as a PostgreSQL DSN from the Supabase Dashboard;
- direct connection and Supavisor session mode on port `5432` as the default service deployment options;
- Supavisor transaction mode on port `6543` as optional and requiring `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0`;
- why `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` are not PostgreSQL DSNs;
- PostgreSQL credentials and Supabase service keys must remain backend-only;
- production schema is managed through `supabase/migrations/*.sql`, not FastAPI startup;
- OpenAI-compatible transport limitation for v1;
- exact contract document link;
- no raw embedding exposure.

Authenticated curl examples must include:

```bash
-H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN"
```

- [ ] **Step 2: Run docs-adjacent tests**

Run:

```bash
pytest tests/test_config.py tests/test_local_knowledge_routes.py -v
```

Expected: tests pass.

- [ ] **Step 3: Commit**

Run:

```bash
git add README.md
git commit -m "docs: document local knowledge retrieval"
```

## Task 8: Final Verification

**Files:**

- No new files.

- [ ] **Step 1: Verify Supabase-specific plan coverage**

Run:

```bash
rg -n "Supabase Deployment Decision|RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE|statement_cache_size|Supavisor|6543|5432|versioned migrations|startup" docs/plans/2026-06-28-local-knowledge-retrieval-layer.md
```

Expected: output includes the deployment decision, config setting, pool setting, README requirements, acceptance criteria, and assumptions.

- [ ] **Step 2: Verify no production DDL runs from startup or service code**

Run:

```bash
rg -n "CREATE TABLE|DROP TABLE|ALTER TABLE|CREATE EXTENSION|CREATE OR REPLACE FUNCTION" main.py app/
```

Expected: no production DDL appears in `main.py`, route modules, or the retrieval service. DDL should exist only in versioned migrations or disposable integration tests.

- [ ] **Step 3: Run focused unit suite**

Run:

```bash
pytest tests/test_config.py tests/test_models.py tests/services/test_local_knowledge_retrieval.py tests/test_local_knowledge_routes.py -v
```

Expected: all focused tests pass.

- [ ] **Step 4: Run broader non-integration suite**

Run:

```bash
pytest -m "not integration" -v
```

Expected: non-integration tests pass.

- [ ] **Step 5: Run integration suite when Docker is available**

Run:

```bash
pytest -m integration -v
```

Expected: integration tests pass when Docker/testcontainers is available.

- [ ] **Step 6: Manual local smoke test**

Start the API with retrieval and auth configuration:

```bash
uvicorn main:app
```

Then call:

```bash
curl http://localhost:8000/knowledge/profiles \
  -H "X-Knowledge-API-Key: $KNOWLEDGE_API_TOKEN"
```

Expected: returns active profile counts for `vinuni-policy`, or a sanitized `503` if the local DB/config is not ready.

- [ ] **Step 7: Commit final fixes if verification required changes**

Run:

```bash
git status --short
git add <changed-files>
git commit -m "fix: stabilize local knowledge retrieval"
```

Skip this commit if `git status --short` is clean.

## Acceptance Criteria

- Existing uploaded-file RAG endpoints keep their current behavior and tests.
- New retrieval behavior is isolated in `app/services/local_knowledge_retrieval.py` and `app/routes/local_knowledge_routes.py`.
- Production retrieval connects to the Supabase PostgreSQL knowledge database with pgvector using a PostgreSQL DSN, not Supabase REST URLs or API keys.
- Direct connection or Supavisor session mode on port `5432` is the default deployment approach for the long-running FastAPI service.
- Supavisor transaction mode on port `6543` is supported by setting `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0`.
- Production schema objects are managed through versioned migrations such as `supabase/migrations/*.sql`.
- Application startup does not create, drop, or alter production Supabase schema.
- The production `vector` extension, `public.knowledge_chunks`, and `public.match_knowledge_chunks(...)` contract are verified before deployment.
- `GET /knowledge/profiles` discovers active profiles, including `embedding_input_version`.
- `POST /knowledge/retrieve` calls `public.match_knowledge_chunks(...)` with a validated 1536-dimensional query embedding and the exact resolved active profile.
- Service operations resolve the active profile once and return profile/rows envelopes.
- Service config fails closed for missing embedding API key, non-1536 dimensions, invalid configured defaults, and non-OpenAI provider slugs without an explicit OpenAI-compatible base URL.
- Service config passes `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE` to asyncpg `statement_cache_size`.
- `LocalKnowledgeRetrievalService.dataset_namespace` is available for routes.
- Shutdown closes both the asyncpg pool and the OpenAI-compatible embedding HTTP client.
- Database failures are wrapped as `RetrievalDatabaseError` and returned as sanitized route errors.
- Direct document and neighbor reads are scoped by `dataset_namespace`, `embedding_profile`, and `is_active is true`.
- Request defaults defer to service config when omitted.
- Whitespace-only retrieval queries are rejected.
- `/knowledge/*` cannot be accessed without configured backend auth.
- `/knowledge/*` returns `503` only when no auth mechanism is configured, and `401` when auth is configured but credentials are invalid.
- Profile mismatches, missing active profiles, multiple unconfigured profiles, inconsistent metadata for the same profile string, wrong dimensions, wrong input version, and non-finite vectors fail closed.
- Route responses do not expose raw exception details, connection strings, provider endpoints, SQL, or embeddings.
- README documents the security boundary, auth, Supabase PostgreSQL DSN requirements, Supavisor connection modes, `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE`, and `RETRIEVAL_*` configuration.

## Assumptions and Defaults

- The new API surface is service plus FastAPI routes.
- Supabase PostgreSQL with pgvector is the production knowledge database target.
- `RETRIEVAL_DATABASE_URL` is a PostgreSQL DSN from the Supabase Dashboard; Supabase REST URLs and API keys are not valid database DSNs.
- Direct connection or Supavisor session mode on port `5432` is the default deployment approach.
- Supavisor transaction mode on port `6543` is opt-in and requires `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE=0`.
- `RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE` defaults to `100` for direct and session-mode connections.
- Production schema changes are migration-driven through versioned SQL files, not application startup.
- Production contract verification is non-destructive and must happen before deployment.
- The current dataset namespace default is `vinuni-policy`.
- The current default profile components are `google-gemini`, `gemini-embedding-001`, `1536`, and input version `v1`.
- `RETRIEVAL_EMBEDDING_PROFILE` may be omitted only when the database has exactly one active profile for the namespace.
- v1 supports OpenAI-compatible embedding transport only; `RETRIEVAL_EMBEDDING_PROVIDER` is validated as a stored provider slug and does not select a generic provider implementation.
- The default `google-gemini` profile requires an explicit `RETRIEVAL_EMBEDDING_BASE_URL`; without it, retrieval fails closed rather than using OpenAI's default endpoint.
- For `embedding_input_version="v1"`, query embedding input is the raw user query string sent as `input=[query]`.
- The current RPC return shape is authoritative for semantic retrieval responses.
- The implementation uses `asyncpg` to stay aligned with existing route-level PostgreSQL access.
