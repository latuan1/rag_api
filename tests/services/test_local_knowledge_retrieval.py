import asyncio
import functools
import math

import pytest

from app.services.local_knowledge_retrieval import (
    DocumentOutput,
    LocalKnowledgeConfig,
    LocalKnowledgeRetrievalService,
    ResolvedEmbeddingProfile,
    RetrievalConfigurationError,
    RetrievalDatabaseError,
    RetrievalEmbeddingError,
    RetrievalOutput,
    normalize_asyncpg_dsn,
    validated_vector_to_pg_literal,
)


def async_test(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakePool:
    def __init__(self, conn, fail_close=False):
        self.conn = conn
        self.close_called = False
        self.fail_close = fail_close

    def acquire(self):
        return FakeAcquire(self.conn)

    async def close(self):
        self.close_called = True
        if self.fail_close:
            raise RuntimeError("pool close failed")


class FakeEmbeddingClient:
    def __init__(self, embedding=None, fail=False):
        self.closed = False
        self.embedding = embedding or [0.1] * 1536
        self.fail = fail
        self.calls = []
        self.embeddings = self

    async def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("provider failed")
        self.calls.append(kwargs)
        return type(
            "EmbeddingResponse",
            (),
            {"data": [type("EmbeddingData", (), {"embedding": self.embedding})()]},
        )()

    async def aclose(self):
        self.closed = True


class FailingCloseEmbeddingClient(FakeEmbeddingClient):
    async def aclose(self):
        self.closed = True
        raise RuntimeError("client close failed")


class FakeConn:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.calls = []
        self.fail = fail

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if self.fail:
            raise RuntimeError("database failed")
        return self.rows


def make_config(**overrides):
    values = {
        "database_url": "postgresql://user:pass@localhost:5432/db",
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


def profile_row(**overrides):
    values = {
        "embedding_profile": "google-gemini:gemini-embedding-001:1536:v1",
        "embedding_provider": "google-gemini",
        "embedding_model": "gemini-embedding-001",
        "embedding_dimensions": 1536,
        "embedding_input_version": "v1",
        "active_chunks": 7,
    }
    values.update(overrides)
    return values


def make_service(rows=None, **config_overrides):
    conn = FakeConn(rows=rows)
    service = LocalKnowledgeRetrievalService(
        make_config(**config_overrides), pool=FakePool(conn)
    )
    return service, conn


def test_normalize_asyncpg_dsn_removes_psycopg2_driver():
    assert (
        normalize_asyncpg_dsn("postgresql+psycopg2://u:p@h/db")
        == "postgresql://u:p@h/db"
    )


def test_normalize_asyncpg_dsn_preserves_supabase_tls_parameters():
    assert (
        normalize_asyncpg_dsn(
            "postgresql+psycopg2://u:p@h:5432/db?sslmode=require"
        )
        == "postgresql://u:p@h:5432/db?sslmode=require"
    )


def test_validated_vector_to_pg_literal_accepts_1536_dimensions():
    literal = validated_vector_to_pg_literal([0.1] * 1536, 1536)

    assert literal.startswith("[0.1,0.1")
    assert literal.endswith("]")


@pytest.mark.parametrize("values", ([0.1] * 1535, [math.inf] * 1536))
def test_validated_vector_to_pg_literal_rejects_bad_vectors(values):
    with pytest.raises(RetrievalEmbeddingError):
        validated_vector_to_pg_literal(values, 1536)


@async_test
async def test_discovery_sql_selects_input_version_and_scopes_active_namespace():
    service, conn = make_service(rows=[profile_row()])

    profiles = await service.discover_active_profiles()

    query, args = conn.calls[0]
    assert "embedding_input_version" in query
    assert "dataset_namespace = $1" in query
    assert "is_active is true" in query
    assert args == ("vinuni-policy",)
    assert profiles[0].embedding_input_version == "v1"


@async_test
async def test_no_active_profiles_fail_closed():
    service, _ = make_service(rows=[])

    with pytest.raises(RetrievalConfigurationError):
        await service.resolve_profile()


@async_test
async def test_multiple_active_profiles_without_explicit_config_fail_closed():
    service, _ = make_service(
        rows=[profile_row(), profile_row(embedding_profile="other")]
    )

    with pytest.raises(RetrievalConfigurationError):
        await service.resolve_profile()


@pytest.mark.parametrize(
    ("config_overrides", "row_overrides"),
    [
        ({"embedding_profile": "inactive"}, {}),
        ({"embedding_provider": "openai"}, {}),
        ({"embedding_model": "text-embedding-3-small"}, {}),
        ({}, {"embedding_dimensions": 1024}),
        ({}, {"embedding_input_version": "v2"}),
    ],
)
@async_test
async def test_profile_mismatches_fail_closed(config_overrides, row_overrides):
    service, _ = make_service(
        rows=[profile_row(**row_overrides)], **config_overrides
    )

    with pytest.raises(RetrievalConfigurationError):
        await service.resolve_profile()


@async_test
async def test_configured_profile_string_with_multiple_metadata_groups_fails_closed():
    profile = "google-gemini:gemini-embedding-001:1536:v1"
    service, _ = make_service(
        rows=[profile_row(), profile_row(embedding_model="other")],
        embedding_profile=profile,
    )

    with pytest.raises(RetrievalConfigurationError):
        await service.resolve_profile()


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": ""},
        {"embedding_dimensions": 1024},
        {"embedding_input_version": "v2"},
        {"default_match_count": 0},
        {"default_match_count": 51},
        {"default_match_threshold": -1.1},
        {"default_match_threshold": 1.1},
        {"database_command_timeout": 0},
        {"embedding_timeout_seconds": 0},
        {"database_statement_cache_size": -1},
    ],
)
def test_base_service_config_rejects_invalid_values(overrides):
    with pytest.raises(RetrievalConfigurationError):
        LocalKnowledgeRetrievalService(make_config(**overrides))


def test_base_service_config_accepts_positive_float_timeouts_and_zero_cache():
    service = LocalKnowledgeRetrievalService(
        make_config(
            database_command_timeout=0.5,
            embedding_timeout_seconds=1.25,
            database_statement_cache_size=0,
        )
    )

    assert service.config.database_statement_cache_size == 0


@async_test
async def test_get_pool_passes_timeout_and_statement_cache(monkeypatch):
    calls = []
    fake_pool = FakePool(FakeConn())

    async def fake_create_pool(**kwargs):
        calls.append(kwargs)
        return fake_pool

    import app.services.local_knowledge_retrieval as module

    monkeypatch.setattr(
        module,
        "asyncpg",
        type("FakeAsyncpg", (), {"create_pool": fake_create_pool}),
    )
    service = LocalKnowledgeRetrievalService(
        make_config(
            database_url="postgresql+psycopg2://u:p@h/db?sslmode=require",
            database_command_timeout=7.5,
            database_statement_cache_size=0,
        )
    )

    assert await service.get_pool() is fake_pool
    assert calls == [
        {
            "dsn": "postgresql://u:p@h/db?sslmode=require",
            "command_timeout": 7.5,
            "statement_cache_size": 0,
        }
    ]


@async_test
async def test_embedding_client_construction_passes_timeout_and_reuses_client(monkeypatch):
    calls = []
    client = FakeEmbeddingClient()

    def fake_openai(**kwargs):
        calls.append(kwargs)
        return client

    import app.services.local_knowledge_retrieval as module

    monkeypatch.setattr(module, "AsyncOpenAI", fake_openai)
    service = LocalKnowledgeRetrievalService(
        make_config(embedding_timeout_seconds=12.5)
    )

    assert await service.embed_query("hello") == [0.1] * 1536
    assert await service.embed_query("again") == [0.1] * 1536
    assert calls == [
        {
            "api_key": "test-key",
            "base_url": "https://example.invalid/openai/",
            "timeout": 12.5,
        }
    ]
    assert client.calls[0]["input"] == ["hello"]
    assert client.calls[0]["dimensions"] == 1536


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_api_key": ""},
        {"embedding_provider": "google-gemini", "embedding_base_url": None},
        {
            "embedding_provider": "openai",
            "embedding_model": "gemini-embedding-001",
            "embedding_base_url": None,
        },
    ],
)
@async_test
async def test_retrieve_fails_closed_when_embedding_config_incomplete(overrides):
    service, _ = make_service(rows=[profile_row()], **overrides)

    with pytest.raises(RetrievalConfigurationError):
        await service.retrieve("hello")


@async_test
async def test_database_only_operations_do_not_validate_embedding_config():
    service, _ = make_service(
        rows=[profile_row()], embedding_api_key="", embedding_base_url=None
    )

    assert await service.discover_active_profiles()
    assert await service.fetch_document("doc-1")
    assert await service.fetch_neighbors("doc-1", ordinal=2, window=1)


def test_dataset_namespace_property_and_lazy_constructor():
    service = LocalKnowledgeRetrievalService(make_config())

    assert service.dataset_namespace == "vinuni-policy"
    assert service._pool is None


@async_test
async def test_close_closes_pool_and_exact_embedding_client():
    pool = FakePool(FakeConn())
    client = FakeEmbeddingClient()
    service = LocalKnowledgeRetrievalService(make_config(), pool=pool)
    service._embedding_client = client

    await service.close()

    assert pool.close_called is True
    assert client.closed is True
    assert service._pool is None
    assert service._embedding_client is None


@async_test
async def test_close_resets_state_and_attempts_all_cleanup_when_one_fails():
    pool = FakePool(FakeConn(), fail_close=True)
    client = FailingCloseEmbeddingClient()
    service = LocalKnowledgeRetrievalService(make_config(), pool=pool)
    service._embedding_client = client

    with pytest.raises(RuntimeError):
        await service.close()

    assert pool.close_called is True
    assert client.closed is True
    assert service._pool is None
    assert service._embedding_client is None


@async_test
async def test_database_failures_are_wrapped():
    service = LocalKnowledgeRetrievalService(
        make_config(), pool=FakePool(FakeConn(fail=True))
    )

    with pytest.raises(RetrievalDatabaseError):
        await service.discover_active_profiles()


@async_test
async def test_retrieve_returns_output_resolves_once_and_applies_defaults(monkeypatch):
    profile = ResolvedEmbeddingProfile(**profile_row())
    service = LocalKnowledgeRetrievalService(make_config(), pool=FakePool(FakeConn()))
    resolve_calls = 0

    async def fake_resolve_profile():
        nonlocal resolve_calls
        resolve_calls += 1
        return profile

    async def fake_embed_query(query):
        return [0.1] * 1536

    async def fake_fetch(query, *args):
        calls.append((query, args))
        return [{"chunk_id": "chunk-1"}]

    calls = []
    monkeypatch.setattr(service, "resolve_profile", fake_resolve_profile)
    monkeypatch.setattr(service, "embed_query", fake_embed_query)
    monkeypatch.setattr(service, "_fetch", fake_fetch)

    output = await service.retrieve("hello", match_count=None, match_threshold=None)

    assert isinstance(output, RetrievalOutput)
    assert output.profile is profile
    assert output.rows == [{"chunk_id": "chunk-1"}]
    assert resolve_calls == 1
    assert calls[0][1][3:5] == (0.0, 10)


@async_test
async def test_retrieve_passes_vector_literal_as_bind_argument(monkeypatch):
    profile = ResolvedEmbeddingProfile(**profile_row())
    service = LocalKnowledgeRetrievalService(make_config(), pool=FakePool(FakeConn()))
    calls = []

    async def fake_resolve_profile():
        return profile

    async def fake_embed_query(query):
        return [0.1] * 1536

    async def fake_fetch(query, *args):
        calls.append((query, args))
        return []

    monkeypatch.setattr(service, "resolve_profile", fake_resolve_profile)
    monkeypatch.setattr(service, "embed_query", fake_embed_query)
    monkeypatch.setattr(service, "_fetch", fake_fetch)

    await service.retrieve("hello", match_count=3, match_threshold=0.2)

    query, args = calls[0]
    assert "$1::vector" in query
    assert args[0].startswith("[0.1,0.1")
    assert args[1:] == ("vinuni-policy", profile.embedding_profile, 0.2, 3, None)


@async_test
async def test_fetch_document_scopes_and_returns_document_output(monkeypatch):
    profile = ResolvedEmbeddingProfile(**profile_row())
    service = LocalKnowledgeRetrievalService(make_config(), pool=FakePool(FakeConn()))
    calls = []

    async def fake_resolve_profile():
        return profile

    async def fake_fetch(query, *args):
        calls.append((query, args))
        return [{"chunk_id": "chunk-1"}]

    monkeypatch.setattr(service, "resolve_profile", fake_resolve_profile)
    monkeypatch.setattr(service, "_fetch", fake_fetch)

    output = await service.fetch_document("doc-1", limit=25)

    assert isinstance(output, DocumentOutput)
    assert output.rows == [{"chunk_id": "chunk-1"}]
    query, args = calls[0]
    assert "dataset_namespace = $1" in query
    assert "embedding_profile = $2" in query
    assert "is_active is true" in query
    assert "document_id = $3" in query
    assert args == ("vinuni-policy", profile.embedding_profile, "doc-1", 25)


@async_test
async def test_fetch_neighbors_scopes_and_bounds_inclusive_ordinals(monkeypatch):
    profile = ResolvedEmbeddingProfile(**profile_row())
    service = LocalKnowledgeRetrievalService(make_config(), pool=FakePool(FakeConn()))
    calls = []

    async def fake_resolve_profile():
        return profile

    async def fake_fetch(query, *args):
        calls.append((query, args))
        return []

    monkeypatch.setattr(service, "resolve_profile", fake_resolve_profile)
    monkeypatch.setattr(service, "_fetch", fake_fetch)

    await service.fetch_neighbors("doc-1", ordinal=1, window=5)

    query, args = calls[0]
    assert "ordinal between $4 and $5" in query
    assert "is_active is true" in query
    assert args == ("vinuni-policy", profile.embedding_profile, "doc-1", 0, 6)
