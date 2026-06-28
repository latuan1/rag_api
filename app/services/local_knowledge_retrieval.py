import asyncio
import inspect
import math
from dataclasses import dataclass
from typing import Any, Iterable

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised only in stripped local runtimes
    asyncpg = None

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised only in stripped local runtimes
    AsyncOpenAI = None


class RetrievalConfigurationError(RuntimeError):
    """Raised when retrieval config does not match active database state."""


class RetrievalEmbeddingError(RuntimeError):
    """Raised when query embedding generation or validation fails."""


class RetrievalDatabaseError(RuntimeError):
    """Raised when the knowledge database cannot be queried."""


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


def normalize_asyncpg_dsn(value: str) -> str:
    return (
        value.replace("postgresql+psycopg2://", "postgresql://", 1)
        .replace("postgresql+asyncpg://", "postgresql://", 1)
    )


def validate_query_embedding(
    values: Iterable[float], expected_dimensions: int
) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != expected_dimensions:
        raise RetrievalEmbeddingError(
            f"query embedding must have {expected_dimensions} dimensions"
        )
    if not all(math.isfinite(value) for value in vector):
        raise RetrievalEmbeddingError("query embedding must contain only finite values")
    return vector


def validated_vector_to_pg_literal(
    values: Iterable[float], expected_dimensions: int
) -> str:
    vector = validate_query_embedding(values, expected_dimensions)
    return "[" + ",".join(str(value) for value in vector) + "]"


def validate_base_config(config: LocalKnowledgeConfig) -> None:
    if not config.database_url:
        raise RetrievalConfigurationError("retrieval database URL is not configured")
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
        raise RetrievalConfigurationError("embedding timeout must be greater than 0")
    if config.database_statement_cache_size < 0:
        raise RetrievalConfigurationError(
            "database statement cache size must be greater than or equal to 0"
        )


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


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)


class LocalKnowledgeRetrievalService:
    def __init__(self, config: LocalKnowledgeConfig, pool: Any | None = None):
        validate_base_config(config)
        self.config = config
        self._pool = pool
        self._pool_lock = asyncio.Lock()
        self._embedding_client: Any | None = None
        self._embedding_client_lock = asyncio.Lock()

    @property
    def dataset_namespace(self) -> str:
        return self.config.dataset_namespace

    async def get_pool(self):
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is None:
                if asyncpg is None:
                    raise RetrievalConfigurationError("asyncpg is not installed")
                self._pool = await asyncpg.create_pool(
                    dsn=normalize_asyncpg_dsn(self.config.database_url),
                    command_timeout=self.config.database_command_timeout,
                    statement_cache_size=self.config.database_statement_cache_size,
                )
        return self._pool

    async def _fetch(self, query: str, *args: Any) -> list[Any]:
        try:
            pool = await self.get_pool()
            async with pool.acquire() as conn:
                return await conn.fetch(query, *args)
        except RetrievalConfigurationError:
            raise
        except Exception as exc:
            raise RetrievalDatabaseError(
                "local knowledge database query failed"
            ) from exc

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
            raise RuntimeError(
                "one or more retrieval resources failed to close"
            ) from errors[0]

    async def discover_active_profiles(self) -> list[ResolvedEmbeddingProfile]:
        rows = await self._fetch(
            """
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
            """,
            self.config.dataset_namespace,
        )
        return [
            ResolvedEmbeddingProfile(
                embedding_profile=row["embedding_profile"],
                embedding_provider=row["embedding_provider"],
                embedding_model=row["embedding_model"],
                embedding_dimensions=row["embedding_dimensions"],
                embedding_input_version=row["embedding_input_version"],
                active_chunks=row["active_chunks"],
            )
            for row in rows
        ]

    async def resolve_profile(self) -> ResolvedEmbeddingProfile:
        profiles = await self.discover_active_profiles()
        if self.config.embedding_profile:
            profiles = [
                profile
                for profile in profiles
                if profile.embedding_profile == self.config.embedding_profile
            ]
            if len(profiles) != 1:
                raise RetrievalConfigurationError(
                    "configured retrieval embedding profile is not uniquely active"
                )
        else:
            if len(profiles) == 0:
                raise RetrievalConfigurationError(
                    "no active retrieval embedding profile found"
                )
            if len(profiles) > 1:
                raise RetrievalConfigurationError(
                    "multiple active retrieval embedding profiles found"
                )

        profile = profiles[0]
        self._validate_resolved_profile(profile)
        return profile

    def _validate_resolved_profile(self, profile: ResolvedEmbeddingProfile) -> None:
        if profile.embedding_provider != self.config.embedding_provider:
            raise RetrievalConfigurationError(
                "active retrieval embedding provider does not match configuration"
            )
        if profile.embedding_model != self.config.embedding_model:
            raise RetrievalConfigurationError(
                "active retrieval embedding model does not match configuration"
            )
        if profile.embedding_dimensions != self.config.embedding_dimensions:
            raise RetrievalConfigurationError(
                "active retrieval embedding dimensions do not match configuration"
            )
        if profile.embedding_input_version != self.config.embedding_input_version:
            raise RetrievalConfigurationError(
                "active retrieval embedding input version does not match configuration"
            )

    async def get_embedding_client(self):
        if self._embedding_client is not None:
            return self._embedding_client
        async with self._embedding_client_lock:
            if self._embedding_client is None:
                if AsyncOpenAI is None:
                    raise RetrievalConfigurationError("openai is not installed")
                self._embedding_client = AsyncOpenAI(
                    api_key=self.config.embedding_api_key,
                    base_url=self.config.embedding_base_url,
                    timeout=self.config.embedding_timeout_seconds,
                )
        return self._embedding_client

    async def embed_query(self, query: str) -> list[float]:
        validate_embedding_config(self.config)
        try:
            client = await self.get_embedding_client()
            response = await client.embeddings.create(
                model=self.config.embedding_model,
                input=[query],
                dimensions=self.config.embedding_dimensions,
            )
            embedding = response.data[0].embedding
        except RetrievalConfigurationError:
            raise
        except Exception as exc:
            raise RetrievalEmbeddingError(
                "local knowledge query embedding failed"
            ) from exc
        return validate_query_embedding(embedding, self.config.embedding_dimensions)

    async def retrieve(
        self,
        query: str,
        match_count: int | None = None,
        match_threshold: float | None = None,
        filter_document_type: str | None = None,
    ) -> RetrievalOutput:
        profile = await self.resolve_profile()
        embedding = await self.embed_query(query)
        vector_literal = validated_vector_to_pg_literal(
            embedding, self.config.embedding_dimensions
        )
        resolved_match_count = (
            self.config.default_match_count if match_count is None else match_count
        )
        resolved_match_threshold = (
            self.config.default_match_threshold
            if match_threshold is None
            else match_threshold
        )
        rows = await self._fetch(
            """
            select *
            from public.match_knowledge_chunks(
                $1::vector,
                $2,
                $3,
                $4,
                $5,
                $6
            )
            """,
            vector_literal,
            self.config.dataset_namespace,
            profile.embedding_profile,
            resolved_match_threshold,
            resolved_match_count,
            filter_document_type,
        )
        return RetrievalOutput(profile=profile, rows=[_row_to_dict(row) for row in rows])

    async def fetch_document(self, document_id: str, limit: int = 500) -> DocumentOutput:
        profile = await self.resolve_profile()
        rows = await self._fetch(
            """
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
              and document_id = $3
              and is_active is true
            order by ordinal asc
            limit $4
            """,
            self.config.dataset_namespace,
            profile.embedding_profile,
            document_id,
            limit,
        )
        return DocumentOutput(profile=profile, rows=[_row_to_dict(row) for row in rows])

    async def fetch_neighbors(
        self, document_id: str, ordinal: int, window: int
    ) -> DocumentOutput:
        profile = await self.resolve_profile()
        start_ordinal = max(0, ordinal - window)
        end_ordinal = ordinal + window
        rows = await self._fetch(
            """
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
              and document_id = $3
              and ordinal between $4 and $5
              and is_active is true
            order by ordinal asc
            """,
            self.config.dataset_namespace,
            profile.embedding_profile,
            document_id,
            start_ordinal,
            end_ordinal,
        )
        return DocumentOutput(profile=profile, rows=[_row_to_dict(row) for row in rows])
