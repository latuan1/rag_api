"""Integration tests for the local knowledge retrieval pgvector contract."""

import pytest
import sqlalchemy
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from app.services.local_knowledge_retrieval import (
    LocalKnowledgeConfig,
    LocalKnowledgeRetrievalService,
)

pytestmark = pytest.mark.integration

PROFILE = "google-gemini:gemini-embedding-001:1536:v1"
OTHER_PROFILE = "openai:text-embedding-3-small:1536:v1"


@pytest.fixture(scope="module")
def knowledge_pg_container():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg2") as container:
        yield container


@pytest.fixture(scope="module")
def knowledge_engine(knowledge_pg_container):
    engine = sqlalchemy.create_engine(knowledge_pg_container.get_connection_url())
    with engine.begin() as conn:
        _recreate_contract_schema(conn)
        _seed_contract_rows(conn)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def service_dsn(knowledge_pg_container):
    return knowledge_pg_container.get_connection_url()


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


def _vector_literal(value: float = 0.1) -> str:
    return "[" + ",".join([str(value)] * 1536) + "]"


def _recreate_contract_schema(conn):
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS extensions"))
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions"))
    conn.execute(text("SET search_path TO public, extensions"))
    database_name = conn.engine.url.database.replace('"', '""')
    conn.execute(
        text(f'ALTER DATABASE "{database_name}" SET search_path TO public, extensions')
    )
    conn.execute(
        text(
            """
            DROP FUNCTION IF EXISTS public.match_knowledge_chunks(
              extensions.vector(1536), text, text, float, int, text
            )
            """
        )
    )
    conn.execute(text("DROP TABLE IF EXISTS public.knowledge_chunks CASCADE"))
    conn.execute(
        text(
            """
            CREATE TABLE public.knowledge_chunks (
              chunk_id text PRIMARY KEY,
              document_id text NOT NULL,
              section_id text,
              ordinal integer NOT NULL,
              content text NOT NULL,
              embedding_content text NOT NULL,
              policy_title text,
              reference_number text,
              document_type text,
              heading text,
              heading_path text[] NOT NULL DEFAULT '{}',
              url_source text,
              source_line_spans jsonb NOT NULL DEFAULT '[]'::jsonb,
              review_required boolean NOT NULL DEFAULT false,
              review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
              data_quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
              warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
              dataset_namespace text NOT NULL,
              embedding_profile text NOT NULL,
              embedding_provider text NOT NULL,
              embedding_model text NOT NULL,
              embedding_dimensions integer NOT NULL,
              embedding_input_version text NOT NULL,
              embedding extensions.vector(1536) NOT NULL,
              is_active boolean NOT NULL DEFAULT true
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION public.match_knowledge_chunks(
              query_embedding extensions.vector(1536),
              match_namespace text,
              match_embedding_profile text,
              match_threshold float,
              match_count int,
              filter_document_type text
            )
            RETURNS TABLE (
              chunk_id text,
              document_id text,
              section_id text,
              ordinal integer,
              content text,
              embedding_content text,
              policy_title text,
              reference_number text,
              document_type text,
              heading text,
              heading_path text[],
              url_source text,
              similarity double precision
            )
            LANGUAGE sql
            STABLE
            AS $$
              SELECT
                kc.chunk_id,
                kc.document_id,
                kc.section_id,
                kc.ordinal,
                kc.content,
                kc.embedding_content,
                kc.policy_title,
                kc.reference_number,
                kc.document_type,
                kc.heading,
                kc.heading_path,
                kc.url_source,
                1 - (kc.embedding <=> query_embedding) AS similarity
              FROM public.knowledge_chunks kc
              WHERE kc.dataset_namespace = match_namespace
                AND kc.embedding_profile = match_embedding_profile
                AND kc.is_active IS TRUE
                AND (
                  filter_document_type IS NULL
                  OR kc.document_type = filter_document_type
                )
                AND 1 - (kc.embedding <=> query_embedding) >= match_threshold
              ORDER BY similarity DESC, kc.ordinal ASC
              LIMIT LEAST(GREATEST(match_count, 1), 50)
            $$;
            """
        )
    )


def _seed_contract_rows(conn):
    rows = [
        {
            "chunk_id": "chunk-active-1",
            "document_id": "policy-001",
            "section_id": "article-1",
            "ordinal": 2,
            "content": "Active policy text",
            "embedding_content": "Active embedded policy text",
            "policy_title": "Academic Regulations",
            "reference_number": "REF-001",
            "document_type": "Policy",
            "heading": "Article 1. Scope",
            "heading_path": ["Academic Regulations", "Article 1. Scope"],
            "url_source": "https://example.edu/policy",
            "source_line_spans": '[{"start": 1, "end": 3}]',
            "review_required": False,
            "review_reasons": "[]",
            "data_quality_flags": '["ocr-clean"]',
            "warnings": "[]",
            "dataset_namespace": "vinuni-policy",
            "embedding_profile": PROFILE,
            "embedding_provider": "google-gemini",
            "embedding_model": "gemini-embedding-001",
            "embedding_dimensions": 1536,
            "embedding_input_version": "v1",
            "embedding": _vector_literal(0.1),
            "is_active": True,
        },
        {
            "chunk_id": "chunk-active-neighbor",
            "document_id": "policy-001",
            "section_id": "article-2",
            "ordinal": 3,
            "content": "Neighbor policy text",
            "embedding_content": "Neighbor embedded policy text",
            "policy_title": "Academic Regulations",
            "reference_number": "REF-001",
            "document_type": "Policy",
            "heading": "Article 2. Procedure",
            "heading_path": ["Academic Regulations", "Article 2. Procedure"],
            "url_source": "https://example.edu/policy",
            "source_line_spans": "[]",
            "review_required": True,
            "review_reasons": '["manual-check"]',
            "data_quality_flags": "[]",
            "warnings": '["low-confidence-heading"]',
            "dataset_namespace": "vinuni-policy",
            "embedding_profile": PROFILE,
            "embedding_provider": "google-gemini",
            "embedding_model": "gemini-embedding-001",
            "embedding_dimensions": 1536,
            "embedding_input_version": "v1",
            "embedding": _vector_literal(0.15),
            "is_active": True,
        },
        {
            "chunk_id": "chunk-inactive",
            "document_id": "policy-001",
            "section_id": "article-old",
            "ordinal": 4,
            "content": "Inactive policy text",
            "embedding_content": "Inactive embedded policy text",
            "policy_title": "Academic Regulations",
            "reference_number": "REF-OLD",
            "document_type": "Policy",
            "heading": "Inactive",
            "heading_path": ["Inactive"],
            "url_source": "https://example.edu/old",
            "source_line_spans": "[]",
            "review_required": False,
            "review_reasons": "[]",
            "data_quality_flags": "[]",
            "warnings": "[]",
            "dataset_namespace": "vinuni-policy",
            "embedding_profile": PROFILE,
            "embedding_provider": "google-gemini",
            "embedding_model": "gemini-embedding-001",
            "embedding_dimensions": 1536,
            "embedding_input_version": "v1",
            "embedding": _vector_literal(0.1),
            "is_active": False,
        },
        {
            "chunk_id": "chunk-other-namespace",
            "document_id": "policy-999",
            "section_id": "article-x",
            "ordinal": 0,
            "content": "Other namespace text",
            "embedding_content": "Other embedded text",
            "policy_title": "Other Policy",
            "reference_number": "REF-999",
            "document_type": "Policy",
            "heading": "Other",
            "heading_path": ["Other"],
            "url_source": "https://example.edu/other",
            "source_line_spans": "[]",
            "review_required": False,
            "review_reasons": "[]",
            "data_quality_flags": "[]",
            "warnings": "[]",
            "dataset_namespace": "other-namespace",
            "embedding_profile": OTHER_PROFILE,
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-small",
            "embedding_dimensions": 1536,
            "embedding_input_version": "v1",
            "embedding": _vector_literal(0.1),
            "is_active": True,
        },
    ]
    insert_sql = text(
        """
        INSERT INTO public.knowledge_chunks (
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
          source_line_spans,
          review_required,
          review_reasons,
          data_quality_flags,
          warnings,
          dataset_namespace,
          embedding_profile,
          embedding_provider,
          embedding_model,
          embedding_dimensions,
          embedding_input_version,
          embedding,
          is_active
        )
        VALUES (
          :chunk_id,
          :document_id,
          :section_id,
          :ordinal,
          :content,
          :embedding_content,
          :policy_title,
          :reference_number,
          :document_type,
          :heading,
          :heading_path,
          :url_source,
          :source_line_spans,
          :review_required,
          :review_reasons,
          :data_quality_flags,
          :warnings,
          :dataset_namespace,
          :embedding_profile,
          :embedding_provider,
          :embedding_model,
          :embedding_dimensions,
          :embedding_input_version,
          :embedding,
          :is_active
        )
        """
    )
    conn.execute(insert_sql, rows)


async def _make_service(service_dsn: str) -> LocalKnowledgeRetrievalService:
    return LocalKnowledgeRetrievalService(make_config(database_url=service_dsn))


async def test_retrieve_returns_active_rows_for_resolved_namespace_and_profile(
    service_dsn, knowledge_engine, monkeypatch
):
    service = await _make_service(service_dsn)

    async def fake_embed_query(query: str) -> list[float]:
        return [0.1] * 1536

    monkeypatch.setattr(service, "embed_query", fake_embed_query)
    try:
        output = await service.retrieve(
            "grade appeal",
            match_count=10,
            match_threshold=0.9,
            filter_document_type="Policy",
        )
    finally:
        await service.close()

    assert output.profile.embedding_profile == PROFILE
    assert output.profile.embedding_input_version == "v1"
    assert [row["chunk_id"] for row in output.rows] == [
        "chunk-active-1",
        "chunk-active-neighbor",
    ]
    assert all(row["document_type"] == "Policy" for row in output.rows)


async def test_fetch_document_returns_active_rows_with_quality_fields(
    service_dsn, knowledge_engine
):
    service = await _make_service(service_dsn)
    try:
        output = await service.fetch_document("policy-001", limit=20)
    finally:
        await service.close()

    assert output.profile.embedding_input_version == "v1"
    assert [row["chunk_id"] for row in output.rows] == [
        "chunk-active-1",
        "chunk-active-neighbor",
    ]
    assert "review_required" in output.rows[0]
    assert "review_reasons" in output.rows[0]
    assert "data_quality_flags" in output.rows[0]
    assert "warnings" in output.rows[0]
    assert output.rows[0]["review_required"] is False


async def test_fetch_neighbors_respects_inclusive_ordinal_range(
    service_dsn, knowledge_engine
):
    service = await _make_service(service_dsn)
    try:
        output = await service.fetch_neighbors("policy-001", ordinal=2, window=1)
    finally:
        await service.close()

    assert [row["ordinal"] for row in output.rows] == [2, 3]
    assert [row["chunk_id"] for row in output.rows] == [
        "chunk-active-1",
        "chunk-active-neighbor",
    ]


async def test_profile_discovery_includes_embedding_input_version(
    service_dsn, knowledge_engine
):
    service = await _make_service(service_dsn)
    try:
        profiles = await service.discover_active_profiles()
    finally:
        await service.close()

    assert len(profiles) == 1
    assert profiles[0].embedding_profile == PROFILE
    assert profiles[0].embedding_input_version == "v1"
    assert profiles[0].active_chunks == 2
