import pytest

from app.config import (
    RAG_HOST,
    RAG_PORT,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    PDF_EXTRACT_IMAGES,
    PDF_OCR_DPI,
    PDF_OCR_ENABLED,
    PDF_OCR_LANGS,
    PDF_OCR_MIN_TEXT_CHARS,
    VECTOR_DB_TYPE,
    load_retrieval_settings,
)


def test_config_defaults():
    assert RAG_HOST is not None
    assert isinstance(RAG_PORT, int)
    assert isinstance(CHUNK_SIZE, int)
    assert isinstance(CHUNK_OVERLAP, int)
    assert isinstance(PDF_EXTRACT_IMAGES, bool)
    assert PDF_OCR_ENABLED is True
    assert PDF_OCR_LANGS == "eng+vie"
    assert PDF_OCR_MIN_TEXT_CHARS == 20
    assert PDF_OCR_DPI == 200
    assert VECTOR_DB_TYPE is not None


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
